from __future__ import annotations

import time
import threading
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set):
        return frozenset(_freeze(item) for item in value)
    return value


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_plain(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _secret_field_path(value: Any, trail: tuple[str, ...] = ()) -> str | None:
    if not isinstance(value, Mapping):
        return None
    for key, item in value.items():
        name = str(key).strip().lower().lstrip("_")
        current = (*trail, str(key))
        if name in {"api_key", "password", "secret", "access_token", "authorization"} or name.endswith("_api_key"):
            return ".".join(current)
        nested = _secret_field_path(item, current)
        if nested:
            return nested
    return None


@dataclass(frozen=True)
class VideoSource:
    path: Path
    video_id: str
    fingerprint: str
    duration_seconds: float
    size_bytes: int

    def __post_init__(self) -> None:
        resolved = self.path.expanduser().resolve()
        if not self.video_id.strip():
            raise ValueError("video_id 不能为空")
        if not self.fingerprint.strip():
            raise ValueError("source fingerprint 不能为空")
        if self.duration_seconds < 0 or self.size_bytes < 0:
            raise ValueError("视频时长和大小不能为负数")
        object.__setattr__(self, "path", resolved)


@dataclass(frozen=True)
class ProcessingOptions:
    asr: Mapping[str, Any] = field(default_factory=dict)
    frames: Mapping[str, Any] = field(default_factory=dict)
    knowledge: Mapping[str, Any] = field(default_factory=dict)
    visual: Mapping[str, Any] = field(default_factory=dict)
    render: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("asr", "frames", "knowledge", "visual", "render"):
            value = getattr(self, name)
            secret = _secret_field_path(value, (name,))
            if secret:
                raise ValueError(f"ProcessingOptions 不得包含秘密字段：{secret}")
            object.__setattr__(self, name, _freeze(value))

    def as_dict(self) -> dict[str, Any]:
        return {
            name: _plain(getattr(self, name))
            for name in ("asr", "frames", "knowledge", "visual", "render")
        }


@dataclass(frozen=True)
class RunPolicy:
    cloud_authorized: bool = False
    content_level: str = "推荐"
    visual_level: str = "auto"
    target_steps: tuple[str, ...] = ()
    force_steps: frozenset[str] = frozenset()
    retry_limit: int = 0

    def __post_init__(self) -> None:
        if self.retry_limit < 0:
            raise ValueError("retry_limit 不能为负数")
        object.__setattr__(self, "target_steps", tuple(dict.fromkeys(self.target_steps)))
        object.__setattr__(self, "force_steps", frozenset(self.force_steps))


@dataclass(frozen=True)
class CloudCredentials:
    api_key: str = field(default="", repr=False, compare=False)
    base_url: str = ""
    models: tuple[str, ...] = ()


@dataclass(frozen=True)
class RuntimeServices:
    cancel_check: Callable[[], bool] = field(default=lambda: False, repr=False, compare=False)
    event_sink: Callable[[dict[str, Any]], None] = field(default=lambda _event: None, repr=False, compare=False)
    progress_sink: Callable[[dict[str, Any]], None] = field(default=lambda _event: None, repr=False, compare=False)
    stage_progress_sink: Callable[[str, str, int], None] = field(
        default=lambda _stage, _message, _percent: None,
        repr=False,
        compare=False,
    )
    clock: Callable[[], float] = field(default=time.monotonic, repr=False, compare=False)
    port_factories: Mapping[str, Callable[[], Any]] = field(default_factory=dict, repr=False, compare=False)
    cloud_budget: Any = field(default=None, repr=False, compare=False)
    credentials: CloudCredentials | None = field(default=None, repr=False, compare=False)
    _instances: dict[str, Any] = field(default_factory=dict, init=False, repr=False, compare=False)
    _port_lock: Any = field(default_factory=threading.Lock, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "port_factories", MappingProxyType(dict(self.port_factories)))

    def cancelled(self) -> bool:
        return bool(self.cancel_check())

    def port(self, name: str) -> Any:
        try:
            factory = self.port_factories[name]
        except KeyError as exc:
            raise KeyError(f"运行服务未配置端口：{name}") from exc
        with self._port_lock:
            if name not in self._instances:
                self._instances[name] = factory()
            return self._instances[name]


@dataclass(frozen=True)
class ProcessingContext:
    run_id: str
    source: VideoSource
    workspace: "WorkspaceLayout"
    options: ProcessingOptions
    policy: RunPolicy
    services: RuntimeServices = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            raise ValueError("run_id 不能为空")

    def public_snapshot(self) -> dict[str, Any]:
        """返回可安全写入事件/状态的快照；运行服务和秘密永不序列化。"""
        return {
            "run_id": self.run_id,
            "source": {
                "path": str(self.source.path),
                "video_id": self.source.video_id,
                "fingerprint": self.source.fingerprint,
                "duration_seconds": self.source.duration_seconds,
                "size_bytes": self.source.size_bytes,
            },
            "workspace": {
                "root": str(self.workspace.root),
                "video_id": self.workspace.video_id,
            },
            "options": self.options.as_dict(),
            "policy": {
                "cloud_authorized": self.policy.cloud_authorized,
                "content_level": self.policy.content_level,
                "visual_level": self.policy.visual_level,
                "target_steps": list(self.policy.target_steps),
                "force_steps": sorted(self.policy.force_steps),
                "retry_limit": self.policy.retry_limit,
            },
        }


from .artifacts import WorkspaceLayout  # noqa: E402  (仅用于运行时类型解析)
