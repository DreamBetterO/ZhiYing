from __future__ import annotations

import json
import os
import threading
import time
from collections import Counter
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .artifacts import SOURCE_MANIFEST, WorkspaceLayout, _atomic_write_json


class RunEventJournal:
    """每次运行的完整、无密钥执行日志单写入者。"""

    _LEGACY_STAGE = {
        "source.probe": "source",
        "audio.extract": "audio",
        "transcript.decode": "transcript",
        "transcript.normalize": "transcript",
        "frames.candidates": "frames",
        "frames.select": "frames",
        "knowledge.plan": "knowledge",
        "knowledge.course_ir": "knowledge",
        "knowledge.units": "knowledge",
        "knowledge.selfcheck": "knowledge",
        "document.assemble": "knowledge",
        "visual.jobs": "knowledge",
        "visual.evidence": "knowledge",
        "frames.semantics": "knowledge",
        "render.bundle": "render",
    }

    def __init__(
        self,
        layout: WorkspaceLayout,
        run_id: str,
        callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.layout = layout
        self.run_id = run_id
        self.callback = callback
        self.events: list[dict[str, Any]] = []
        self.jsonl_path = layout.state_dir / "runs" / f"{run_id}.jsonl"
        self.text_path = layout.state_dir / "runs" / f"{run_id}.log"
        self.summary_path = layout.state_dir / "runs" / f"{run_id}.summary.json"
        self.state_path = layout.state_dir / "pipeline-state.json"
        self.started_at = ""
        self.finished_at = ""
        self.metadata: dict[str, Any] = {}
        self.finished = False
        self._sequence = 0
        self._started_monotonic = 0.0
        self._lock = threading.RLock()

    @staticmethod
    def _now() -> str:
        return datetime.now().astimezone().isoformat(timespec="milliseconds")

    @classmethod
    def _sanitize(cls, value: Any, key: str = "") -> Any:
        normalized = key.strip().lower()
        if normalized in {"api_key", "authorization", "password", "secret", "credentials"} or normalized.endswith("_api_key"):
            return "<redacted>"
        if isinstance(value, Mapping):
            return {str(item_key): cls._sanitize(item, str(item_key)) for item_key, item in value.items()}
        if isinstance(value, (list, tuple, set, frozenset)):
            return [cls._sanitize(item) for item in value]
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)

    def start(self, metadata: dict[str, Any]) -> None:
        if self.started_at:
            raise RuntimeError(f"运行日志已经开始：{self.run_id}")
        self.started_at = self._now()
        self._started_monotonic = time.monotonic()
        self.metadata = dict(self._sanitize(metadata))
        self.publish({
            "timestamp": self.started_at,
            "type": "run_lifecycle",
            "step_id": "run",
            "stage": "run",
            "level": "info",
            "status": "running",
            "code": "run_started",
            "message": "运行开始",
            "metadata": self.metadata,
        })
        self._write_summary("running")

    def finish(
        self,
        status: str,
        *,
        outputs: dict[str, Any] | None = None,
        error: BaseException | None = None,
        traceback_text: str = "",
    ) -> None:
        if self.finished:
            return
        self.finished_at = self._now()
        error_payload = None
        if error is not None:
            error_payload = {
                "exception_type": type(error).__name__,
                "message": str(error),
                "traceback": traceback_text,
            }
        self.publish({
            "timestamp": self.finished_at,
            "type": "run_lifecycle",
            "step_id": "run",
            "stage": "run",
            "level": "error" if status == "failed" else "warning" if status == "cancelled" else "info",
            "status": status,
            "code": f"run_{status}",
            "message": "运行完成" if status == "succeeded" else "运行已取消" if status == "cancelled" else f"运行失败：{error}",
            "outputs": dict(self._sanitize(outputs or {})),
            "error": error_payload,
        })
        self.finished = True
        self._write_summary(status, outputs=outputs, error=error_payload)

    def publish(self, event: dict[str, Any]) -> None:
        with self._lock:
            row = dict(self._sanitize(event))
            self._sequence += 1
            row.setdefault("timestamp", self._now())
            row.setdefault("run_id", self.run_id)
            row.setdefault("sequence", self._sequence)
            row.setdefault("process_id", os.getpid())
            row.setdefault("thread", threading.current_thread().name)
            row.setdefault(
                "elapsed_seconds",
                round(max(0.0, time.monotonic() - self._started_monotonic), 3)
                if self._started_monotonic else 0.0,
            )
            row.setdefault("step_id", "runtime")
            row.setdefault("code", "runtime_event")
            row.setdefault("stage", str(row["step_id"]).split(".", 1)[0])
            row.setdefault("level", "info")
            row.setdefault("message", "")
            self.events.append(row)
            self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
            with self.jsonl_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n")
            details = []
            for key in ("status", "cache_reason", "error_code", "duration_seconds"):
                value = row.get(key)
                if value not in (None, ""):
                    details.append(f"{key}={value}")
            suffix = f" ({', '.join(details)})" if details else ""
            with self.text_path.open("a", encoding="utf-8") as stream:
                stream.write(
                    f"{row['timestamp']} #{row['sequence']} [{str(row.get('level', 'info')).upper()}] "
                    f"{row.get('step_id', 'runtime')} {row.get('code', 'runtime_event')} "
                    f"{row.get('message', '')}{suffix}\n"
                )
            if row.get("type") == "step_state":
                self._update_state(row)
                self._update_legacy_manifest(row)
                if self.started_at and not self.finished:
                    self._write_summary("running")
            elif row.get("type") == "step_lifecycle" and self.started_at and not self.finished:
                self._write_summary("running")
            elif row.get("level") in {"warning", "error"} and self.started_at and not self.finished:
                self._write_summary("running")
            callback = self.callback
        if callback:
            callback(dict(row))

    def _write_summary(
        self,
        status: str,
        *,
        outputs: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> None:
        latest_steps: dict[str, dict[str, Any]] = {}
        for event in self.events:
            if event.get("type") in {"step_lifecycle", "step_state"}:
                current = latest_steps.setdefault(str(event.get("step_id")), {})
                current.update({
                    "status": event.get("status"),
                    "error_code": event.get("error_code"),
                    "capability": event.get("capability"),
                    "duration_seconds": event.get("duration_seconds"),
                    "timestamp": event.get("timestamp"),
                })
                if event.get("type") == "step_state":
                    current["diagnostics"] = event.get("diagnostics", {})
        event_types = Counter(str(event.get("type", "unknown")) for event in self.events)
        levels = Counter(str(event.get("level", "info")) for event in self.events)
        notable_events = [
            {
                "sequence": event.get("sequence"),
                "level": event.get("level"),
                "step_id": event.get("step_id"),
                "code": event.get("code"),
                "message": event.get("message"),
            }
            for event in self.events if event.get("level") in {"warning", "error"}
        ][-50:]
        payload = {
            "schema_version": 1,
            "run_id": self.run_id,
            "status": status,
            "started_at": self.started_at,
            "finished_at": self.finished_at or None,
            "metadata": self.metadata,
            "event_count": len(self.events),
            "event_types": dict(sorted(event_types.items())),
            "levels": dict(sorted(levels.items())),
            "notable_events": notable_events,
            "steps": latest_steps,
            "outputs": dict(self._sanitize(outputs or {})),
            "error": self._sanitize(error),
            "files": {
                "events": str(self.jsonl_path),
                "readable_log": str(self.text_path),
                "summary": str(self.summary_path),
            },
        }
        _atomic_write_json(self.summary_path, payload)

    def _update_state(self, row: dict[str, Any]) -> None:
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            state = {"schema_version": 1, "run_id": self.run_id, "steps": {}}
        state["run_id"] = self.run_id
        state.setdefault("steps", {})[str(row["step_id"])] = {
            "status": row.get("status"),
            "error_code": row.get("error_code"),
            "capability": row.get("capability"),
            "updated_at": row.get("timestamp"),
        }
        _atomic_write_json(self.state_path, state)

    def _update_legacy_manifest(self, row: dict[str, Any]) -> None:
        legacy_stage = self._LEGACY_STAGE.get(str(row.get("step_id")))
        if not legacy_stage or row.get("status") not in {"cached", "succeeded", "degraded"}:
            return
        manifest_path = self.layout.artifact_paths(SOURCE_MANIFEST)[0]
        if not manifest_path.is_file():
            return
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return
        artifacts = {
            item.get("artifact_id"): item.get("path")
            for item in row.get("artifacts", [])
            if isinstance(item, dict)
        }
        details: dict[str, Any] = {
            "completed_at": row.get("timestamp"),
            "step_id": row.get("step_id"),
            "status": row.get("status"),
        }
        if legacy_stage == "audio":
            details["path"] = artifacts.get("audio.flac")
        elif legacy_stage == "transcript":
            details["path"] = artifacts.get("transcript.normalized")
        elif legacy_stage == "knowledge":
            details["path"] = artifacts.get("document.v2")
        elif legacy_stage == "render":
            for item in row.get("artifacts", []):
                path = Path(str(item.get("path", "")))
                if path.suffix.lower() == ".md":
                    details["markdown"] = str(path)
                elif path.suffix.lower() == ".docx":
                    details["docx"] = str(path)
                elif path.suffix.lower() == ".pdf":
                    details["pdf"] = str(path)
            details["pdf_mode"] = (
                row.get("diagnostics", {}).get("pdf_mode")
                or manifest.get("stages", {}).get("render", {}).get("pdf_mode")
                or "built_in"
            )
        manifest.setdefault("stages", {})[legacy_stage] = details
        try:
            _atomic_write_json(manifest_path, manifest)
        except OSError:
            # 兼容 manifest 可能被桌面进程或安全软件短暂占用；执行事实已写入
            # JSONL/state，不能让这份派生索引反向破坏业务 Step 的终态。
            return
