"""Windows 全局单实例互斥锁。

覆盖 `desktop` 与 `run` 命令；`doctor` 和 `play-url` 不持有资源锁。
进程崩溃后 Windows 自动释放 mutex，不使用固定超时假定实例仍存活。
"""
from __future__ import annotations

import ctypes
import hashlib
import sys
from dataclasses import dataclass

_MUTEX_NAME_PREFIX = "Global\\ZhiYing-"


@dataclass(frozen=True)
class SingleInstanceHandle:
    """持有 Windows named mutex 的句柄；退出时自动释放。"""
    mutex_name: str
    _handle: int = 0

    def __enter__(self) -> "SingleInstanceHandle":
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.release()

    def release(self) -> None:
        if self._handle:
            try:
                ctypes.windll.kernel32.ReleaseMutex(self._handle)
                ctypes.windll.kernel32.CloseHandle(self._handle)
            except (OSError, AttributeError):
                pass
            object.__setattr__(self, "_handle", 0)


def _resource_key(project_root: str) -> str:
    digest = hashlib.sha256(project_root.encode("utf-8")).hexdigest()[:12]
    return f"{_MUTEX_NAME_PREFIX}{digest}"


def acquire_single_instance(project_root: str) -> SingleInstanceHandle:
    """尝试获取全局单实例锁；失败时抛出 RuntimeError。"""
    if sys.platform != "win32":
        raise RuntimeError("单实例互斥仅在 Windows 上可用")
    mutex_name = _resource_key(project_root)
    # CREATE_MUTEX: 初始无主
    handle = ctypes.windll.kernel32.CreateMutexW(None, False, mutex_name)
    last_error = ctypes.windll.kernel32.GetLastError()
    if not handle:
        raise RuntimeError("无法创建全局互斥量")
    if last_error == 183:  # ERROR_ALREADY_EXISTS
        ctypes.windll.kernel32.CloseHandle(handle)
        raise RuntimeError("知影已有任务实例正在运行")
    return SingleInstanceHandle(mutex_name, handle)
