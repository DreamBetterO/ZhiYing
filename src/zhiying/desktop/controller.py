from __future__ import annotations

import queue
import threading
from dataclasses import replace
from pathlib import Path

from ..application.processing import ProcessingService
from ..application.requests import AggregateRequest, JobHandle, JobRequest, ProcessingHandle, ProcessingRequest, ProcessingResult
from . import STAGE_LABELS, format_duration
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
        self._aggregate_cancel = threading.Event()

    def add(self, paths: list[Path]) -> None:
        self._require_idle("添加视频")
        existing = {item.path.resolve() for item in self.items if item.path is not None}
        for raw in paths:
            path = raw.expanduser().resolve()
            if path not in existing:
                item = QueueItem(path)
                self.items.append(item)
                existing.add(path)
        self._emit("queue")

    def add_url(self, url: str) -> None:
        """添加链接源：后台预检+下载，完成后进入「已就绪」，发布 source_ready 事件。"""
        self._require_idle("添加链接")
        source_url = str(url or "").strip()
        if not source_url:
            raise ValueError("链接不能为空")
        if any(item.source_kind == "url" and item.source_url == source_url for item in self.items):
            raise ValueError("该链接已在队列中")
        item = QueueItem(source_kind="url", source_url=source_url, selected=False)
        item.status, item.stage = "下载中", "downloading"
        self.items.append(item)
        self._emit("queue")

        def worker() -> None:
            try:
                def download_progress(event) -> None:
                    percent = int(event.get("percent") or 0)
                    item.progress = max(0, min(100, percent))
                    item.detail = (
                        "正在合并音视频" if event.get("phase") == "merge"
                        else f"下载中 {percent}%"
                    )
                    self._emit("progress", item, "正在下载视频", item.detail)

                acquired = self.service.download_url(
                    source_url, progress=download_progress,
                    cancel_check=lambda: self.state == DesktopState.CANCELLING,
                )
                if self.state == DesktopState.CANCELLING:
                    item.status, item.stage = "已取消", "cancelled"
                    self._emit("queue", item, "下载已取消")
                    return
                item.path = Path(str(acquired["path"]))
                item.detail_title = str(acquired.get("title") or item.source_url)
                item.detail = ""
                item.status, item.stage, item.progress = "已就绪", "ready", 100
                download_cached = bool(acquired.get("cached"))
                message = (
                    "已复用本地缓存，无需重新下载；勾选后点击“生成本地文档”即可开始"
                    if download_cached else
                    "视频已下载完成；勾选后点击“生成本地文档”即可开始"
                )
                self._emit("source_ready", item, message)
                self._emit("queue", item, message)
            except BaseException as exc:
                item.status, item.stage = "失败", "failed"
                item.message = str(exc)
                item.detail = ""
                self._emit("error", item, str(exc))
                self._emit("queue", item, str(exc))

        threading.Thread(target=worker, name="zhiying-url-download", daemon=True).start()

    def remove_selected(self) -> None:
        self._require_idle("移除视频")
        self.items = [item for item in self.items if not item.checked]
        self._emit("queue")

    def reorder(self, source_index: int, target_index: int) -> bool:
        """确定性重排：将 source_index 处的行移动到 target_index 位置。"""
        self._require_idle("调整视频顺序")
        if source_index < 0 or source_index >= len(self.items):
            raise ValueError(f"source_index {source_index} 越界")
        if target_index < 0 or target_index >= len(self.items):
            raise ValueError(f"target_index {target_index} 越界")
        if source_index == target_index:
            return False
        item = self.items.pop(source_index)
        self.items.insert(target_index, item)
        self.aggregate_result = {}
        self._emit("queue", message="已调整视频顺序")
        return True

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
                if self.state == DesktopState.CANCELLING:
                    self.state = DesktopState.CANCELLED
                    self._emit("state", message="已取消")
                    return
                self.state = DesktopState.RUNNING
                self._emit("state", message="开始处理")
                requests = tuple(request_factory(item) for item in selected)
                handle = self.service.process_job(JobRequest(requests))
                self._handle = handle
                if self.state == DesktopState.CANCELLING:
                    handle.cancel()
                handle.subscribe(lambda event: self._on_job_runtime(selected, event))
                job_result = handle.wait()
                for item, result in zip(selected, job_result.video_results):
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

        threading.Thread(target=run_queue, name="zhiying-controller", daemon=True).start()

    def _on_job_runtime(self, selected: list[QueueItem], event: dict) -> None:
        index = int(event.get("source_index", -1))
        if index < 0 or index >= len(selected):
            return
        item = selected[index]
        if event.get("type") == "job_video_started":
            self.current_item = item
            item.status = "处理中"
            if item.started_at is None:
                item.begin()
            self._emit("queue", item, "开始处理")
            return
        self._on_runtime(item, event)

    def cancel(self) -> None:
        if self.state == DesktopState.CANCELLING:
            return
        if self.state not in {DesktopState.PREPARING, DesktopState.RUNNING}:
            return
        self.state = DesktopState.CANCELLING
        if self._handle:
            self._handle.cancel()
        else:
            self._aggregate_cancel.set()
        self._emit("state", message="正在取消")

    def aggregate(self, request: AggregateRequest) -> None:
        if self.state in {DesktopState.PREPARING, DesktopState.RUNNING, DesktopState.CANCELLING}:
            raise RuntimeError("处理任务进行中，暂时无法合并文档")
        self.state = DesktopState.PREPARING
        self._aggregate_cancel.clear()
        self._emit("state", message="正在准备合并文档")

        def worker() -> None:
            try:
                if self.state == DesktopState.CANCELLING:
                    self.state = DesktopState.CANCELLED
                    self._emit("state", message="文档合并已取消")
                    return
                self.state = DesktopState.RUNNING
                self._emit("state", message="正在合并文档")
                result = self.service.aggregate(replace(
                    request, cancel_check=self._aggregate_cancel.is_set,
                ))
                if self.state == DesktopState.CANCELLING:
                    self.state = DesktopState.CANCELLED
                    self._emit("state", message="文档合并已取消")
                    return
                self.aggregate_result = result.to_legacy()
                self.state = DesktopState.COMPLETED
                self._emit("aggregate", message="文档合并完成", payload=self.aggregate_result)
            except BaseException as exc:
                if self.state == DesktopState.CANCELLING:
                    self.state = DesktopState.CANCELLED
                    self._emit("state", message="文档合并已取消")
                else:
                    self.state = DesktopState.FAILED
                    self._emit("error", message=str(exc))

        threading.Thread(target=worker, name="zhiying-aggregate", daemon=True).start()

    def aggregate_local(self, results: tuple[ProcessingResult, ...]) -> None:
        """在后台线程执行完全离线的保守聚合。"""
        if self.state in {DesktopState.PREPARING, DesktopState.RUNNING, DesktopState.CANCELLING}:
            raise RuntimeError("处理任务进行中，暂时无法合并文档")
        if len(results) < 2:
            raise ValueError("至少需要两个已完成的视频才能合并文档")
        self.state = DesktopState.PREPARING
        self._aggregate_cancel.clear()
        self._emit("state", message="正在准备合并本地文档")

        def worker() -> None:
            try:
                if self.state == DesktopState.CANCELLING:
                    self.state = DesktopState.CANCELLED
                    self._emit("state", message="本地文档合并已取消")
                    return
                self.state = DesktopState.RUNNING
                self._emit("state", message="正在合并本地文档")
                result = self.service.local_aggregate(
                    results, cancel_check=self._aggregate_cancel.is_set,
                )
                if self.state == DesktopState.CANCELLING:
                    self.state = DesktopState.CANCELLED
                    self._emit("state", message="本地文档合并已取消")
                    return
                self.aggregate_result = result.to_legacy()
                self.state = DesktopState.COMPLETED
                self._emit("aggregate", message="本地文档合并完成", payload=self.aggregate_result)
            except BaseException as exc:
                if self.state == DesktopState.CANCELLING:
                    self.state = DesktopState.CANCELLED
                    self._emit("state", message="本地文档合并已取消")
                else:
                    self.state = DesktopState.FAILED
                    self._emit("error", message=str(exc))

        threading.Thread(target=worker, name="zhiying-local-aggregate", daemon=True).start()

    def clear_selected_cache(self) -> None:
        """清除所选视频的 Workspace、Output 和失效的派生聚合结果；保留原视频与链接源下载文件。"""
        self._require_idle("清除缓存")
        for item in [value for value in self.items if value.checked]:
            if item.path is not None:
                self.service.delete_video_workspace(item.path)
            item.result = {}
            item.stage, item.status, item.progress = "queued", "等待中", 0
            item.message, item.started_at, item.elapsed, item.eta, item.estimating = "", None, 0.0, None, False
        self.aggregate_result = {}
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

        raw_message = str(event.get("message") or "")
        if raw_message:
            item.message = raw_message

        if event.get("progress") is not None:
            candidate = max(0, min(100, int(event["progress"])))
            if candidate >= 100:
                item.progress = 100
            elif candidate > item.progress:
                # UI 展示采用平滑递增，避免缓存/阶段权重导致一开始直接跳到 50%+。
                item.progress = min(candidate, item.progress + 3)
        eta = event.get("eta_seconds", nested.get("eta_seconds"))
        item.eta = None if eta is None else max(0.0, float(eta))
        item.estimating = False
        item.update_elapsed()

        detail_parts: list[str] = []
        progress_label = f"{item.progress}%"
        if item.stage and item.stage != "completed":
            detail_parts.append(STAGE_LABELS.get(item.stage, item.stage))
        detail_parts.append(progress_label)
        if item.elapsed > 0:
            detail_parts.append(f"已用 {format_duration(item.elapsed)}")
        code = str(event.get("code", ""))
        if code.startswith("asr_") and event.get("level") in {"warning", "error"}:
            detail_parts.append(str(event.get("message", "")))
        item.detail = " · ".join(detail_parts)
        self._emit("progress", item, item.message, item.detail)

    def _complete_item(self, item: QueueItem, result: ProcessingResult) -> None:
        degraded = result.diagnostics.get("status") == "degraded"
        item.stage, item.status, item.progress = "completed", ("已完成（降级）" if degraded else "已完成"), 100
        item.result = result.to_legacy()
        item.finish_timing()
        item.detail = "；".join(result.diagnostics.get("degradation_summary", ())) if degraded else "已完成"
        self._emit("progress", item, "处理完成（存在降级）" if degraded else "处理完成", item.status)

    def _require_idle(self, action: str) -> None:
        if self.state in {DesktopState.PREPARING, DesktopState.RUNNING, DesktopState.CANCELLING}:
            raise RuntimeError(f"处理中不能{action}")

    def _item(self, path: Path) -> QueueItem:
        resolved = path.resolve()
        for item in self.items:
            if item.path is not None and item.path.resolve() == resolved:
                return item
            if item.source_url and Path(item.source_url).resolve() == resolved:
                return item
        raise KeyError(path)

    def _emit(self, kind: str, item: QueueItem | None = None, message: str = "", detail: str = "", payload=None) -> None:
        self.events.put(UiEvent(
            kind, self.state, item.path if item else None, message,
            item.detail if (item and not detail) else detail,
            item.progress if item else 0,
            dict(payload or {}),
        ))
