from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from threading import Event, Lock
from typing import Any, Callable


@dataclass(frozen=True)
class CloudAuthorization:
    authorized: bool
    api_key: str = field(default="", repr=False, compare=False)
    base_url: str = ""
    models: tuple[str, ...] = ()
    allowed_data: tuple[str, ...] = ("transcript", "source_ids")
    max_calls: int = 0
    editorial_brief: str = ""

    def legacy_settings(self, base: dict[str, Any]) -> dict[str, Any]:
        return {
            **base, "_runtime_api_key": self.api_key, "_runtime_base_url": self.base_url,
            "_runtime_models": list(self.models), "_runtime_max_calls": self.max_calls,
            "_runtime_editorial_brief": self.editorial_brief,
        }


@dataclass(frozen=True)
class ProcessingRequest:
    video: Path | None = None
    url: str | None = None
    action: str = "process"
    content_level: str = "推荐"
    visual_level: str = "auto"
    speech_models: tuple[str, ...] = ("faster-whisper",)
    cloud: CloudAuthorization | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if (self.video is None) == (self.url is None):
            raise ValueError("ProcessingRequest 必须且只能提供 video 或 url 之一")


@dataclass(frozen=True)
class AggregateRequest:
    results: tuple["ProcessingResult", ...]
    cloud: CloudAuthorization = field(repr=False, compare=False)
    cancel_check: Callable[[], bool] | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class ProcessingResult:
    video_id: str
    manifest: Path
    markdown: Path
    docx: Path
    pdf: Path
    mode: str = ""
    model: str = ""
    cloud_usage: dict[str, Any] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_legacy(cls, value: dict[str, Any]) -> "ProcessingResult":
        known = {key: value.get(key) for key in ("video_id", "manifest", "markdown", "docx", "pdf")}
        return cls(
            video_id=str(known["video_id"] or ""),
            manifest=Path(known["manifest"] or ""), markdown=Path(known["markdown"] or ""),
            docx=Path(known["docx"] or ""), pdf=Path(known["pdf"] or ""),
            mode=str(value.get("mode") or ""), model=str(value.get("model") or ""),
            cloud_usage=dict(value.get("cloud_usage") or {}),
            diagnostics={key: item for key, item in value.items() if key not in {*known, "mode", "model", "cloud_usage"}},
        )

    def to_legacy(self) -> dict[str, Any]:
        return {
            "video_id": self.video_id, "manifest": self.manifest, "markdown": self.markdown,
            "docx": self.docx, "pdf": self.pdf, "mode": self.mode, "model": self.model,
            "cloud_usage": dict(self.cloud_usage), **dict(self.diagnostics),
        }


class ProcessingHandle:
    def __init__(self) -> None:
        self._cancel = Event()
        self._done = Event()
        self._result: ProcessingResult | None = None
        self._error: BaseException | None = None
        self._subscribers: list[Callable[[dict[str, Any]], None]] = []
        self._pending_events: list[dict[str, Any]] = []
        self._subscriber_lock = Lock()

    def cancel(self) -> None:
        self._cancel.set()

    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def subscribe(self, callback: Callable[[dict[str, Any]], None]) -> None:
        with self._subscriber_lock:
            self._subscribers.append(callback)
            pending = self._pending_events
            self._pending_events = []
        for event in pending:
            self._notify(callback, event)

    def publish(self, event: dict[str, Any]) -> None:
        value = dict(event)
        with self._subscriber_lock:
            callbacks = tuple(self._subscribers)
            if not callbacks:
                self._pending_events.append(value)
                return
        for callback in callbacks:
            self._notify(callback, value)

    @staticmethod
    def _notify(callback: Callable[[dict[str, Any]], None], event: dict[str, Any]) -> None:
        """UI/观察者故障不能反向中断业务线程。"""
        try:
            callback(dict(event))
        except Exception:
            return

    def finish(self, result: ProcessingResult) -> None:
        self._result = result
        self._done.set()

    def fail(self, error: BaseException) -> None:
        self._error = error
        self._done.set()

    def wait(self, timeout: float | None = None) -> ProcessingResult:
        if not self._done.wait(timeout):
            raise TimeoutError("处理尚未完成")
        if self._error is not None:
            raise self._error
        if self._result is None:
            raise RuntimeError("处理未返回结果")
        return self._result

    @property
    def done(self) -> bool:
        return self._done.is_set()
