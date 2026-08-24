from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Callable, Iterator

from .contracts import ExecutionCancelled


class ResourceLeaseManager:
    """Process-wide named leases for scarce local resources."""

    _guard = threading.Lock()
    _locks: dict[str, threading.Lock] = {}

    @classmethod
    def _lock(cls, name: str) -> threading.Lock:
        with cls._guard:
            return cls._locks.setdefault(str(name), threading.Lock())

    @classmethod
    @contextmanager
    def acquire(
        cls,
        name: str,
        *,
        cancel_check: Callable[[], bool] = lambda: False,
    ) -> Iterator[None]:
        lock = cls._lock(name)
        while not lock.acquire(timeout=0.1):
            if cancel_check():
                raise ExecutionCancelled(f"等待资源 {name} 时任务已取消")
        try:
            yield
        finally:
            lock.release()
