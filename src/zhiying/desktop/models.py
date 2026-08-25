from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
import time
from typing import Any


class DesktopState(str, Enum):
    IDLE = "idle"
    PREPARING = "preparing"
    RUNNING = "running"
    CANCELLING = "cancelling"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class QueueItem:
    path: Path | None = None
    source_kind: str = "local"          # local / url
    source_url: str = ""                # 链接源原始 URL（仅 source_kind == "url"）
    detail_title: str = ""              # 链接源真实标题（下载完成后由 controller 回填）
    selected: bool = True
    status: str = "等待中"
    stage: str = "queued"
    progress: int = 0
    message: str = ""
    detail: str = ""
    started_at: float | None = None
    elapsed: float = 0.0
    eta: float | None = None
    estimating: bool = False
    result: dict[str, Any] = field(default_factory=dict)
    history: dict[str, Any] = field(default_factory=dict)

    @property
    def resolved_path(self) -> Path:
        if self.path is not None:
            return self.path
        if self.source_url:
            return Path(self.source_url)
        raise ValueError("队列项缺少本地路径与来源链接")

    def begin(self) -> None:
        """开始新一轮处理：重置计时与进度展示，保证上一阶段（下载/整理）的 100% 不延续。"""
        self.started_at = time.monotonic()
        self.elapsed = 0.0
        self.eta = None
        self.estimating = True
        self.progress = 0
        self.stage = "queued"
        self.detail = ""
        self.message = "正在准备"

    def update_elapsed(self) -> None:
        if self.started_at is not None:
            self.elapsed = max(0.0, time.monotonic() - self.started_at)

    def finish_timing(self) -> None:
        self.update_elapsed()
        self.started_at = None
        self.eta = 0.0
        self.estimating = False

    @property
    def checked(self) -> bool:
        return self.selected

    @checked.setter
    def checked(self, value: bool) -> None:
        self.selected = bool(value)


@dataclass(frozen=True)
class UiEvent:
    kind: str
    state: DesktopState
    item_path: Path | None = None
    message: str = ""
    detail: str = ""
    progress: int = 0
    payload: dict[str, Any] = field(default_factory=dict)
