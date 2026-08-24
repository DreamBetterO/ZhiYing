from __future__ import annotations

import json
import statistics
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .utils import write_json


_ETA_HISTORY_VERSION = 2
_MAX_HISTORY_ROWS_PER_BUCKET = 20
_MAX_HISTORY_ROWS_TOTAL = 400
_WARM_SAMPLE_COUNT = 5
_CACHE_STATES = {"unknown", "hit", "miss", "not_applicable"}


@dataclass(frozen=True)
class ProgressEvent:
    stage: str
    unit_kind: str
    completed: float
    total: float
    cache_hit: bool = False
    duration_seconds: float | None = None
    task_id: str = ""
    cache_state: str = ""
    bucket: str = ""


class EtaEstimator:
    """Estimate remaining time from real task units instead of UI percentages."""

    def __init__(
        self,
        history_path: Path | None = None,
        *,
        hardware: str = "",
        model: str = "",
        content_level: str = "",
    ) -> None:
        self.history_path = history_path
        self.context = {
            "hardware": str(hardware).strip(),
            "model": str(model).strip(),
            "content_level": str(content_level).strip(),
        }
        self._events: dict[str, ProgressEvent] = {}
        self._history = self._load_history()
        self._lock = threading.RLock()

    def _load_history(self) -> list[dict[str, Any]]:
        if self.history_path is None or not self.history_path.is_file():
            return []
        try:
            payload = json.loads(self.history_path.read_text(encoding="utf-8"))
        except (OSError, TypeError, json.JSONDecodeError):
            return []
        if int(payload.get("version", 0) or 0) != _ETA_HISTORY_VERSION:
            return []
        rows = payload.get("samples", [])
        if not isinstance(rows, list):
            return []
        return [dict(row) for row in rows[-_MAX_HISTORY_ROWS_TOTAL:] if isinstance(row, dict)]

    @staticmethod
    def _history_key(row: dict[str, Any]) -> tuple[str, ...]:
        return (
            str(row.get("stage", "")),
            str(row.get("unit_kind", "")),
            str(row.get("bucket", "")),
            str(row.get("cache_state", "")),
            str(row.get("hardware", "")),
            str(row.get("model", "")),
            str(row.get("content_level", "")),
        )

    def _trim_history(self) -> None:
        kept: list[dict[str, Any]] = []
        counts: dict[tuple[str, ...], int] = {}
        for row in reversed(self._history):
            key = self._history_key(row)
            if counts.get(key, 0) >= _MAX_HISTORY_ROWS_PER_BUCKET:
                continue
            counts[key] = counts.get(key, 0) + 1
            kept.append(row)
            if len(kept) >= _MAX_HISTORY_ROWS_TOTAL:
                break
        self._history = list(reversed(kept))

    def _save_history(self) -> None:
        if self.history_path is None:
            return
        write_json(self.history_path, {
            "version": _ETA_HISTORY_VERSION,
            "samples": self._history,
        })

    def register(self, events: list[ProgressEvent]) -> None:
        for event in events:
            self.observe(event)

    def observe(self, event: ProgressEvent) -> None:
        if not str(event.stage).strip() or not str(event.unit_kind).strip():
            raise ValueError("ProgressEvent stage/unit_kind 不能为空")
        completed = max(0.0, float(event.completed))
        total = max(0.0, float(event.total))
        if completed > total:
            completed = total
        cache_state = str(event.cache_state or ("hit" if event.cache_hit else "miss")).strip()
        if cache_state not in _CACHE_STATES:
            raise ValueError(f"无效 cache_state：{cache_state}")
        task_id = str(event.task_id).strip() or f"{event.stage}.{event.unit_kind}"
        normalized = ProgressEvent(
            stage=str(event.stage),
            unit_kind=str(event.unit_kind),
            completed=completed,
            total=total,
            cache_hit=cache_state == "hit",
            duration_seconds=(
                None if event.duration_seconds is None
                else max(0.0, float(event.duration_seconds))
            ),
            task_id=task_id,
            cache_state=cache_state,
            bucket=str(event.bucket).strip(),
        )
        with self._lock:
            self._events[task_id] = normalized
            if (
                normalized.duration_seconds is not None
                and normalized.duration_seconds > 0
                and normalized.cache_state in {"hit", "miss"}
            ):
                self._history.append({
                    "stage": normalized.stage,
                    "unit_kind": normalized.unit_kind,
                    "cache_hit": normalized.cache_hit,
                    "cache_state": normalized.cache_state,
                    "bucket": normalized.bucket,
                    "duration_seconds": round(normalized.duration_seconds, 6),
                    **self.context,
                })
                self._trim_history()
                self._save_history()

    def _matching_samples(self, event: ProgressEvent) -> list[float]:
        samples: list[float] = []
        for row in self._history:
            if (
                str(row.get("stage", "")) != event.stage
                or str(row.get("unit_kind", "")) != event.unit_kind
                or str(row.get("bucket", "")) != event.bucket
            ):
                continue
            if event.cache_state != "unknown" and str(row.get("cache_state", "")) != event.cache_state:
                continue
            if any(
                self.context[name]
                and str(row.get(name, "")) != self.context[name]
                for name in self.context
            ):
                continue
            try:
                value = float(row.get("duration_seconds", 0.0))
            except (TypeError, ValueError):
                continue
            if value > 0:
                samples.append(value)
        return samples[-_WARM_SAMPLE_COUNT:]

    def estimate(self) -> float | None:
        with self._lock:
            remaining_seconds = 0.0
            has_remaining = False
            for event in self._events.values():
                remaining = max(0.0, event.total - event.completed)
                if remaining <= 0:
                    continue
                has_remaining = True
                if event.cache_state == "unknown":
                    return None
                if event.cache_state == "not_applicable":
                    continue
                samples = self._matching_samples(event)
                if not samples:
                    return None
                remaining_seconds += statistics.median(samples) * remaining
            return max(0.0, remaining_seconds) if has_remaining else 0.0

    def clear(self) -> None:
        with self._lock:
            self._events.clear()
