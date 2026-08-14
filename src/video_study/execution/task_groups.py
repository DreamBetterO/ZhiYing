from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Mapping

from .artifacts import canonical_json_hash


class FileTaskGroupCache:
    """Runner 所有的动态子任务缓存；领域代码只看到 load/record 窄接口。"""

    def __init__(self, state_dir: Path, group_id: str) -> None:
        self.root = state_dir / "cache" / "groups" / self._safe(group_id)

    @staticmethod
    def _safe(value: str) -> str:
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", value) or "task"

    def load(self, task_id: str, fingerprint: Mapping[str, Any]) -> dict[str, Any] | None:
        path = self.root / f"{self._safe(task_id)}.json"
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        if value.get("fingerprint") != canonical_json_hash(dict(fingerprint)):
            return None
        result = value.get("result")
        return dict(result) if isinstance(result, dict) else None

    def record(self, task_id: str, fingerprint: Mapping[str, Any], result: Mapping[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{self._safe(task_id)}.json"
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        value = {
            "schema_version": 1,
            "group_id": self.root.name,
            "task_id": task_id,
            "fingerprint": canonical_json_hash(dict(fingerprint)),
            "result": dict(result),
        }
        try:
            temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
            json.loads(temporary.read_text(encoding="utf-8"))
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
