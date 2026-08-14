from __future__ import annotations

import queue
import threading
from pathlib import Path

from ..application.processing import ProcessingService
from ..application.requests import AggregateRequest, ProcessingHandle, ProcessingRequest, ProcessingResult
from .models import DesktopState, QueueItem, UiEvent


class DesktopController:
    def __init__(self, service: ProcessingService) -> None:
        self.service = service
        self.items: list[QueueItem] = []
        self.state = DesktopState.IDLE
        self.events: queue.Queue[UiEvent] = queue.Queue()
        self._handle: ProcessingHandle | None = None
        self.aggregate_result: dict = {}
        self.current_item: QueueItem | None = None

    def add(self, paths: list[Path]) -> None:
        self._require_idle("添加视频")
        existing = {item.path.resolve() for item in self.items}
        for raw in paths:
            path = raw.expanduser().resolve()
            if path not in existing:
                cached = self.service.cached_result(path)
                item = QueueItem(path)
                if cached:
                    item.status, item.stage, item.progress = "已完成", "completed", 100
                    item.result = cached.to_legacy()
                self.items.append(item)
                existing.add(path)
        self._emit("queue")

    def remove_selected(self) -> None:
        self._require_idle("移除视频")
        self.items = [item for item in self.items if not item.checked]
        self._emit("queue")

    def move_selected(self, direction: int) -> bool:
        self._require_idle("调整视频顺序")
        if direction not in {-1, 1}:
            raise ValueError("视频移动方向必须是 -1 或 1")
        moved = False
        indices = range(len(self.items)) if direction < 0 else range(len(self.items) - 1, -1, -1)
        for index in indices:
            target = index + direction
            if not self.items[index].checked or target < 0 or target >= len(self.items):
                continue
            if self.items[target].checked:
                continue
            self.items[index], self.items[target] = self.items[target], self.items[index]
            moved = True
        if moved:
            self.aggregate_result = {}
            self._emit("queue", message="已调整视频顺序")
        return moved

    def move_selected_up(self) -> bool:
        return self.move_selected(-1)

    def move_selected_down(self) -> bool:
        return self.move_selected(1)

    def select(self, path: Path, selected: bool) -> None:
        item = self._item(path)
        item.checked = selected
        self._emit("queue", item)

    def toggle_all(self) -> None:
        self._require_idle("切换选择")
        target = not all(item.checked for item in self.items)
        for item in self.items:
            item.checked = target
        self._emit("queue")

    def start(self, request_factory) -> None:
        if self.state in {DesktopState.PREPARING, DesktopState.RUNNING, DesktopState.CANCELLING}:
            raise RuntimeError("当前已有任务在运行")
        selected = [item for item in self.items if item.checked]
        if not selected:
            raise ValueError("请先选择视频")
        self.state = DesktopState.PREPARING
        self._emit("state", message="正在准备")

        def run_queue() -> None:
            try:
                self.state = DesktopState.RUNNING
                self._emit("state", message="开始处理")
                for item in selected:
                    if self.state == DesktopState.CANCELLING:
                        break
                    cached = self.service.cached_result(item.path)
                    if cached:
                        self._complete_item(item, cached)
                        continue
                    self.current_item = item
                    item.status = "处理中"
                    item.begin()
                    request: ProcessingRequest = request_factory(item.path)
                    handle = self.service.process(request)
                    self._handle = handle
                    handle.subscribe(lambda event, current=item: self._on_runtime(current, event))
                    result = handle.wait()
                    self._complete_item(item, result)
                if self.state == DesktopState.CANCELLING:
                    self.state = DesktopState.CANCELLED
                    self._emit("state", message="已取消")
                else:
                    self.state = DesktopState.COMPLETED
                    self._emit("state", message="处理完成")
            except BaseException as exc:
                if self.state == DesktopState.CANCELLING:
                    self.state = DesktopState.CANCELLED
                    self._emit("state", message="已取消")
                else:
                    self.state = DesktopState.FAILED
                    if self.current_item is not None:
                        self.current_item.update_elapsed()
                        self.current_item.started_at = None
                        self.current_item.eta = None
                        self.current_item.estimating = False
                        self.current_item.status = "失败"
                        self.current_item.stage = "failed"
                        self.current_item.message = str(exc)
                    self._emit("error", message=str(exc))
            finally:
                self._handle = None
                self.current_item = None

        threading.Thread(target=run_queue, name="video-study-controller", daemon=True).start()

    def cancel(self) -> None:
        if self.state == DesktopState.CANCELLING:
            return
        if self.state not in {DesktopState.PREPARING, DesktopState.RUNNING}:
            return
        self.state = DesktopState.CANCELLING
        if self._handle:
            self._handle.cancel()
        self._emit("state", message="正在取消")

    def aggregate(self, request: AggregateRequest) -> None:
        if self.state in {DesktopState.PREPARING, DesktopState.RUNNING, DesktopState.CANCELLING}:
            raise RuntimeError("处理中不能聚合")
        self.state = DesktopState.PREPARING
        self._emit("state", message="正在准备聚合")

        def worker() -> None:
            try:
                self.state = DesktopState.RUNNING
                self._emit("state", message="正在聚合")
                result = self.service.aggregate(request)
                self.aggregate_result = result.to_legacy()
                self.state = DesktopState.COMPLETED
                self._emit("aggregate", message="聚合完成", payload=self.aggregate_result)
            except BaseException as exc:
                self.state = DesktopState.FAILED
                self._emit("error", message=str(exc))

        threading.Thread(target=worker, name="video-study-aggregate", daemon=True).start()

    def delete_selected(self) -> None:
        self._require_idle("删除产物")
        for item in [value for value in self.items if value.checked]:
            self.service.delete_video_workspace(item.path)
            item.result = {}
            item.stage, item.status, item.progress = "queued", "等待中", 0
            item.message, item.started_at, item.elapsed, item.eta, item.estimating = "", None, 0.0, None, False
        self._emit("queue")

    def clear_workspace(self) -> int:
        self._require_idle("清空缓存")
        removed = self.service.clear_workspace()
        for item in self.items:
            item.result = {}
            item.stage, item.status, item.progress = "queued", "等待中", 0
            item.message, item.started_at, item.elapsed, item.eta, item.estimating = "", None, 0.0, None, False
        self.aggregate_result = {}
        self._emit("queue")
        return removed

    def _on_runtime(self, item: QueueItem, event: dict) -> None:
        nested = event.get("event", {}) if event.get("type") == "task_progress" else {}
        if not isinstance(nested, dict):
            nested = {
                key: getattr(nested, key) for key in (
                    "stage", "completed", "total", "eta_seconds",
                ) if hasattr(nested, key)
            }
        stage = event.get("step_id") if event.get("type") == "step_state" else event.get("stage")
        item.stage = str(stage or nested.get("stage") or item.stage)
        item.message = str(event.get("message") or item.message)
        if event.get("progress") is not None:
            item.progress = max(0, min(100, int(event["progress"])))
        elif nested.get("total"):
            fraction = float(nested.get("completed", 0)) / float(nested["total"])
            item.progress = max(item.progress, min(99, int(fraction * 100)))
        eta = event.get("eta_seconds", nested.get("eta_seconds"))
        item.eta = None if eta is None else max(0.0, float(eta))
        item.estimating = eta is None and item.started_at is not None
        item.update_elapsed()
        self._emit("progress", item, item.message)

    def _complete_item(self, item: QueueItem, result: ProcessingResult) -> None:
        item.stage, item.status, item.progress = "completed", "已完成", 100
        item.result = result.to_legacy()
        item.finish_timing()
        self._emit("progress", item, "处理完成")

    def _require_idle(self, action: str) -> None:
        if self.state in {DesktopState.PREPARING, DesktopState.RUNNING, DesktopState.CANCELLING}:
            raise RuntimeError(f"处理中不能{action}")

    def _item(self, path: Path) -> QueueItem:
        resolved = path.resolve()
        for item in self.items:
            if item.path.resolve() == resolved:
                return item
        raise KeyError(path)

    def _emit(self, kind: str, item: QueueItem | None = None, message: str = "", payload=None) -> None:
        self.events.put(UiEvent(
            kind, self.state, item.path if item else None, message, item.progress if item else 0,
            dict(payload or {}),
        ))
