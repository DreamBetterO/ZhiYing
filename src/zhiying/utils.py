from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def repair_structured_text_controls(value: Any) -> Any:
    """递归修复模型 JSON 将 LaTeX 转义解析成的 XML 非法控制字符。"""
    if isinstance(value, dict):
        return {key: repair_structured_text_controls(item) for key, item in value.items()}
    if isinstance(value, list):
        return [repair_structured_text_controls(item) for item in value]
    if not isinstance(value, str):
        return value
    recovered = value.replace("\x08", r"\b").replace("\x0b", r"\v").replace("\x0c", r"\f")
    return "".join(
        char if char in "\t\n\r" or ord(char) >= 0x20 else f"\\x{ord(char):02x}"
        for char in recovered
    )


def cloud_request_limit(settings: dict[str, Any]) -> int:
    """Resolve the configured per-video cloud call budget and optional env override."""
    budget = settings.get("budget", {}) if isinstance(settings, dict) else {}
    configured = budget.get("max_calls_per_video", 1)
    env_name = str(settings.get("max_calls_env", "") or "") if isinstance(settings, dict) else ""
    env_value = os.getenv(env_name) if env_name else None
    raw = env_value if env_value and env_value.isdigit() else configured
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 1


def cloud_output_limit(
    settings: dict[str, Any],
    key: str = "max_output_tokens",
    default: int = 5000,
) -> int:
    """Resolve a positive, stage-specific output limit and optional env override."""
    budget = settings.get("budget", {}) if isinstance(settings, dict) else {}
    configured = budget.get(key, default)
    env_name = str(settings.get(f"{key}_env", "") or "") if isinstance(settings, dict) else ""
    env_value = os.getenv(env_name) if env_name else None
    raw = env_value if env_value and env_value.isdigit() else configured
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return max(1, int(default))


def cloud_optional_output_limit(
    settings: dict[str, Any],
    key: str,
    default: int | None = None,
) -> int | None:
    """Resolve an optional provider hard cap; zero disables the API max_tokens field."""
    budget = settings.get("budget", {}) if isinstance(settings, dict) else {}
    configured = budget.get(key, default)
    env_name = str(settings.get(f"{key}_env", "") or "") if isinstance(settings, dict) else ""
    env_value = os.getenv(env_name) if env_name else None
    raw = env_value if env_value not in (None, "") else configured
    if raw in (None, ""):
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default if default is None else max(1, int(default))
    return value if value > 0 else None


def cloud_timeout_limit(
    settings: dict[str, Any],
    key: str = "timeout_seconds",
    default: float = 240.0,
) -> float:
    """Resolve a positive cloud runtime limit and optional environment override."""
    configured = settings.get(key, default) if isinstance(settings, dict) else default
    env_name = str(settings.get(f"{key}_env", "") or "") if isinstance(settings, dict) else ""
    env_value = os.getenv(env_name) if env_name else None
    raw = env_value if env_value else configured
    try:
        return max(1.0, float(raw))
    except (TypeError, ValueError):
        return max(1.0, float(default))


def background_process_kwargs() -> dict[str, int]:
    """Return platform flags that keep tool/model subprocesses behind the GUI."""
    if os.name != "nt":
        return {}
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}


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
        **background_process_kwargs(),
    )


class TaskCancelled(RuntimeError):
    """用户主动终止任务。"""


def ensure_not_cancelled(cancel_check=None) -> None:
    if cancel_check and cancel_check():
        raise TaskCancelled("任务已由用户取消")


def emit_runtime_event(
    settings: dict[str, Any] | None,
    stage: str,
    level: str,
    message: str,
    **details: Any,
) -> None:
    """向桌面层发送结构化运行事件；没有回调时保持纯本地、无副作用。"""
    if not isinstance(settings, dict):
        return
    callback = settings.get("_event_callback")
    if not callable(callback):
        return
    callback({
        "timestamp": now_iso(),
        "stage": str(stage),
        "level": str(level),
        "message": str(message),
        **details,
    })


def terminate_process(process: subprocess.Popen, *, grace_seconds: float = 1.0) -> None:
    """终止本应用启动的子进程；Windows 下同时结束其模型/工具子进程树。"""
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError:
            pass
    else:
        try:
            process.terminate()
        except OSError:
            pass
    try:
        process.wait(timeout=max(0.1, grace_seconds))
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            pass


def run_cancellable(
    command: list[str], *, cancel_check=None, timeout_seconds: float | None = None,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        command, text=True, encoding="utf-8", errors="replace",
        **background_process_kwargs(),
    )
    started_at = time.monotonic()
    try:
        while process.poll() is None:
            if cancel_check and cancel_check():
                terminate_process(process)
                raise TaskCancelled("任务已由用户取消")
            if timeout_seconds is not None and time.monotonic() - started_at >= timeout_seconds:
                terminate_process(process)
                raise subprocess.TimeoutExpired(command, timeout_seconds)
            time.sleep(0.2)
        if process.returncode:
            raise subprocess.CalledProcessError(process.returncode, command)
        return subprocess.CompletedProcess(command, process.returncode, "", "")
    finally:
        if process.poll() is None:
            terminate_process(process)


@dataclass(frozen=True)
class ProcessResult:
    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


class LocalProcessAdapter:
    """统一的可取消本地命令 adapter；诊断只保留有界、脱敏文本。"""

    def __init__(self, *, diagnostic_limit: int = 4000) -> None:
        self.diagnostic_limit = max(256, int(diagnostic_limit))

    @staticmethod
    def _redact(text: str) -> str:
        sanitized = re.sub(
            r"(?i)(api[-_ ]?key|authorization|bearer|token|password)(\s*[:=]\s*|\s+)[^\s,;]+",
            r"\1\2<redacted>",
            text,
        )
        return sanitized

    def run(
        self,
        command,
        *,
        cancel_check=None,
        timeout_seconds: float | None = None,
        cwd: Path | None = None,
    ) -> ProcessResult:
        process = subprocess.Popen(
            list(command),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(cwd) if cwd else None,
            **background_process_kwargs(),
        )
        started_at = time.monotonic()
        stdout_lines: deque[str] = deque(maxlen=256)
        stderr_lines: deque[str] = deque(maxlen=256)

        def drain(stream, target: deque[str]) -> None:
            if stream is None:
                return
            for line in iter(stream.readline, ""):
                target.append(str(line))

        stdout_thread = threading.Thread(target=drain, args=(process.stdout, stdout_lines), daemon=True)
        stderr_thread = threading.Thread(target=drain, args=(process.stderr, stderr_lines), daemon=True)
        stdout_thread.start()
        stderr_thread.start()
        try:
            while process.poll() is None:
                if cancel_check and cancel_check():
                    terminate_process(process)
                    raise TaskCancelled("任务已由用户取消")
                if timeout_seconds is not None and time.monotonic() - started_at >= timeout_seconds:
                    terminate_process(process)
                    raise TimeoutError(f"本地命令超时（{timeout_seconds:g} 秒）")
                time.sleep(0.05)
            stdout_thread.join(timeout=1.0)
            stderr_thread.join(timeout=1.0)
            safe_stdout = self._redact("".join(stdout_lines))[-self.diagnostic_limit:]
            safe_stderr = self._redact("".join(stderr_lines))[-self.diagnostic_limit:]
            if process.returncode:
                message = safe_stderr or safe_stdout or f"exit code {process.returncode}"
                raise RuntimeError(f"本地命令失败：{message}")
            return ProcessResult(tuple(str(item) for item in command), process.returncode, safe_stdout, safe_stderr)
        finally:
            if process.poll() is None:
                terminate_process(process)


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
