from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def safe_name(value: str, max_length: int = 80) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" ._")
    value = re.sub(r"\s+", " ", value)
    return (value or "video")[:max_length]


def quick_fingerprint(path: Path, block_size: int = 1024 * 1024) -> str:
    stat = path.stat()
    digest = hashlib.sha256()
    digest.update(str(stat.st_size).encode())
    with path.open("rb") as handle:
        digest.update(handle.read(block_size))
        if stat.st_size > block_size:
            handle.seek(max(0, stat.st_size - block_size))
            digest.update(handle.read(block_size))
    return digest.hexdigest()[:12]


def run(command: list[str], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )


class TaskCancelled(RuntimeError):
    """用户主动终止任务。"""


def ensure_not_cancelled(cancel_check=None) -> None:
    if cancel_check and cancel_check():
        raise TaskCancelled("任务已由用户取消")


def run_cancellable(command: list[str], *, cancel_check=None) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(command, text=True, encoding="utf-8", errors="replace")
    try:
        while process.poll() is None:
            if cancel_check and cancel_check():
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                raise TaskCancelled("任务已由用户取消")
            time.sleep(0.2)
        if process.returncode:
            raise subprocess.CalledProcessError(process.returncode, command)
        return subprocess.CompletedProcess(command, process.returncode, "", "")
    finally:
        if process.poll() is None:
            process.kill()


def hhmmss(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def srt_time(seconds: float) -> str:
    millis = max(0, int(round(seconds * 1000)))
    hours, remainder = divmod(millis, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, ms = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"
