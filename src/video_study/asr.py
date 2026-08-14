from __future__ import annotations

import json
import multiprocessing
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from .transcript import (
    apply_terminology_corrections,
    clamp_segment_timestamps,
    normalize_transcript,
    write_srt,
)
from .utils import TaskCancelled, emit_runtime_event, ensure_not_cancelled, now_iso, terminate_process, write_json
from .media import prepare_cuda_runtime


def _normalize_segment_timestamps(data: dict, settings: dict[str, Any]) -> tuple[dict, bool]:
    """兼容入口；生产 normalize Step 使用 transcript 领域纯函数。"""
    try:
        duration = float(settings.get("_duration_seconds", 0.0))
    except (TypeError, ValueError):
        return data, False
    return clamp_segment_timestamps(data, duration)


def _write_srt(output_srt: Path, rows: list[dict]) -> None:
    write_srt(output_srt, rows)


def _initial_prompt(settings: dict[str, Any], context: str | None = None) -> str | None:
    """合并人工热词与视频标题；标题往往包含课程最关键的领域术语。"""
    terms: list[str] = []
    for item in [*settings.get("hotwords", []), context or ""]:
        value = str(item).strip()
        if value and value not in terms:
            terms.append(value)
    return "，".join(terms) or None


def _faster_whisper_worker(
    connection,
    audio: str,
    model_path: str,
    settings: dict[str, Any],
    device: str,
    context: str | None,
) -> None:
    """隔离 faster-whisper 推理，使父进程可立即终止长时间首段推理。"""
    try:
        if device == "cuda":
            prepare_cuda_runtime()
        from faster_whisper import WhisperModel

        compute_type = settings.get("compute_type", "int8_float16") if device == "cuda" else "int8"
        model = WhisperModel(model_path, device=device, compute_type=compute_type)
        prompt = _initial_prompt(settings, context)
        segments, info = model.transcribe(
            audio,
            language=settings.get("language", "zh"),
            beam_size=int(settings.get("beam_size", 5)),
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
            initial_prompt=prompt,
            word_timestamps=False,
            condition_on_previous_text=True,
        )
        rows: list[dict[str, Any]] = []
        duration = max(1.0, float(settings.get("duration_seconds", 1.0)))
        for index, segment in enumerate(segments, start=1):
            text = segment.text.strip()
            if text:
                rows.append({
                    "segment_id": f"seg_{index:05d}",
                    "start_seconds": round(float(segment.start), 3),
                    "end_seconds": round(float(segment.end), 3),
                    "text": text,
                    "avg_logprob": round(float(segment.avg_logprob), 4),
                    "no_speech_prob": round(float(segment.no_speech_prob), 4),
                })
            connection.send({"kind": "progress", "fraction": min(0.99, float(segment.end) / duration)})
        connection.send({
            "kind": "done",
            "rows": rows,
            "info": {
                "language": getattr(info, "language", settings.get("language", "zh")),
                "language_probability": float(getattr(info, "language_probability", 0.0)),
            },
            "compute_type": compute_type,
        })
    except BaseException as exc:
        try:
            connection.send({"kind": "error", "error_type": type(exc).__name__, "message": str(exc)[-700:]})
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        connection.close()


def _stop_worker(process: multiprocessing.Process) -> None:
    if not process.is_alive():
        return
    process.terminate()
    process.join(timeout=1.0)
    if process.is_alive():
        try:
            process.kill()
        except AttributeError:
            pass
        process.join(timeout=1.0)


def _transcribe_once(
    audio: Path,
    model_path: str,
    settings: dict[str, Any],
    device: str,
    context: str | None = None,
):
    worker_settings = {
        key: value for key, value in settings.items()
        if not key.startswith("_") and not callable(value)
    }
    worker_settings["duration_seconds"] = max(1.0, float(settings.get("_duration_seconds", 1.0)))
    receive, send = multiprocessing.get_context("spawn").Pipe(duplex=False)
    process = multiprocessing.get_context("spawn").Process(
        target=_faster_whisper_worker,
        args=(send, str(audio), model_path, worker_settings, device, context),
        daemon=True,
        name="video-study-faster-whisper",
    )
    process.start()
    send.close()
    callback = settings.get("_progress_callback")
    final: dict[str, Any] | None = None
    try:
        while process.is_alive() or receive.poll():
            if settings.get("_cancel_check") and settings["_cancel_check"]():
                _stop_worker(process)
                raise TaskCancelled("任务已由用户取消")
            if not receive.poll(0.1):
                continue
            try:
                message = receive.recv()
            except EOFError:
                break
            if message.get("kind") == "progress" and callback:
                callback(float(message.get("fraction", 0.0)))
            elif message.get("kind") == "error":
                raise RuntimeError(f"{message.get('error_type', 'ASR')}：{message.get('message', '识别失败')}")
            elif message.get("kind") == "done":
                final = message
                break
        process.join(timeout=1.0)
        if final is None:
            raise RuntimeError(f"faster-whisper 子进程异常退出（exit={process.exitcode}）")
        return list(final.get("rows", [])), dict(final.get("info", {})), str(final.get("compute_type", ""))
    finally:
        receive.close()
        if process.is_alive():
            _stop_worker(process)


def transcribe(
    audio: Path,
    output_json: Path,
    output_srt: Path,
    model_dir: Path,
    settings: dict,
    force: bool = False,
    context: str | None = None,
) -> dict:
    # 兼容 I/O 入口：不再读取或判定缓存；生产缓存唯一归 WorkspaceCache 所有。
    ensure_not_cancelled(settings.get("_cancel_check"))
    raw = decode_audio(audio, output_json.parent, model_dir, settings, context)
    duration = float(settings.get("_duration_seconds", 0.0) or 0.0)
    data = normalize_transcript(raw, settings, duration)
    data["runtime"] = {
        "cache_hit": False, "device": data.get("device"), "engine": data.get("engine"),
    }
    write_json(output_json, data)
    _write_srt(output_srt, data["segments"])
    return data


def decode_audio(
    audio: Path,
    temporary_dir: Path,
    model_dir: Path,
    settings: dict[str, Any],
    context: str | None = None,
) -> dict[str, Any]:
    """只执行 provider 解码；不读缓存、不纠错、不 clamp、不写标准 Artifact。"""
    engine = str(settings.get("engine", "faster-whisper"))
    if engine == "qwen3-asr-0.6b":
        return _decode_qwen_audio(audio, temporary_dir, settings, context)
    requested = settings.get("device", "auto")
    device = "cuda" if requested == "auto" else requested
    fallbacks: list[dict[str, str]] = []
    emit_runtime_event(
        settings, "asr", "info",
        f"faster-whisper 正在隔离进程中加载模型并尝试 {'GPU' if device == 'cuda' else 'CPU'}",
        code="asr_model_loading", requested_device=device,
    )
    try:
        segments, info, compute_type = _transcribe_once(audio, str(model_dir), settings, device, context)
    except TaskCancelled:
        raise
    except Exception as exc:
        if device != "cuda":
            raise
        fallback_message = f"GPU 识别不可用，已降级到 CPU：{type(exc).__name__}: {exc}"
        fallbacks.append({"code": "asr_cuda_fallback", "message": fallback_message})
        emit_runtime_event(
            settings, "asr", "warning", fallback_message,
            code="asr_cuda_fallback", device="cpu",
        )
        device = "cpu"
        segments, info, compute_type = _transcribe_once(audio, str(model_dir), settings, device, context)
    rows = list(segments)
    raw = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "model": str(model_dir),
        "engine": "faster-whisper",
        "device": device,
        "compute_type": compute_type,
        "language": info.get("language", settings.get("language", "zh")),
        "language_probability": round(float(info.get("language_probability", 0.0)), 4),
        "initial_prompt": _initial_prompt(settings, context),
        "segments": rows,
        "fallbacks": fallbacks,
    }
    emit_runtime_event(
        settings, "asr", "info",
        f"语音识别完成：faster-whisper · {'GPU' if device == 'cuda' else 'CPU'} · {compute_type}",
        code="asr_completed", device="gpu" if device == "cuda" else "cpu",
        compute_type=compute_type, segment_count=len(raw["segments"]),
    )
    return raw


class SpeechAdapter:
    """显式注入运行服务后调用现有 ASR 实现的薄 adapter。"""

    def __init__(
        self,
        model_dir: Path,
        *,
        config_root: Path,
        cancel_check,
        event_sink=lambda _event: None,
    ) -> None:
        self.model_dir = model_dir
        self.config_root = config_root
        self.cancel_check = cancel_check
        self.event_sink = event_sink

    def probe_capability(self) -> dict[str, Any]:
        return {"model_dir": str(self.model_dir), "available": self.model_dir.is_dir()}

    def decode(
        self,
        audio: Path,
        output: Path,
        options: dict[str, Any],
        *,
        cancel_check,
        progress=None,
    ) -> dict:
        settings = dict(options)
        settings["_config_root"] = str(self.config_root)
        settings["_cancel_check"] = cancel_check or self.cancel_check
        settings["_event_callback"] = self.event_sink
        if progress:
            settings["_progress_callback"] = progress
        raw = decode_audio(
            audio, output.parent, self.model_dir, settings,
            context=str(options.get("context", "")),
        )
        write_json(output, raw)
        return raw


def _decode_qwen_audio(
    audio: Path, temporary_dir: Path, settings: dict[str, Any], context: str | None,
) -> dict:
    config_root = Path(str(settings.get("_config_root", Path.cwd()))).resolve()

    def resolve_setting(value: str | Path) -> Path:
        candidate = Path(os.path.expandvars(str(value)))
        return candidate.resolve() if candidate.is_absolute() else (config_root / candidate).resolve()

    python = resolve_setting(os.getenv(
        "QWEN_ASR_RUNTIME_PYTHON", str(settings.get("qwen_runtime_python", sys.executable))
    ))
    runtime = resolve_setting(settings.get("qwen_runtime_dir", "models/qwen3-asr-runtime"))
    model = resolve_setting(settings.get("qwen_model_dir", "models/qwen3-asr-0.6b"))
    runner = resolve_setting(settings.get("qwen_runner", "scripts/qwen_asr_runner.py"))
    for path, label in ((python, "Qwen ASR Python"), (runtime, "Qwen ASR 运行库"), (model, "Qwen ASR 模型"), (runner, "Qwen ASR runner")):
        if not path.exists():
            raise FileNotFoundError(f"{label}不存在：{path}")
    temporary_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, dir=temporary_dir) as handle:
        temporary = Path(handle.name)
    try:
        emit_runtime_event(
            settings, "asr", "info", "Qwen3-ASR 正在隔离进程中加载模型；可随时取消",
            code="asr_model_loading", requested_device="auto",
        )
        process = subprocess.Popen([
            str(python), str(runner), "--runtime", str(runtime), "--model", str(model),
            "--audio", str(audio), "--output", str(temporary), "--language", str(settings.get("language", "zh")),
            "--chunk-seconds", str(max(15, int(settings.get("qwen_chunk_seconds", 60)))),
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        callback = settings.get("_progress_callback")
        duration = max(1.0, float(settings.get("_duration_seconds", 1.0)))
        started = __import__("time").monotonic()
        while process.poll() is None:
            if settings.get("_cancel_check") and settings["_cancel_check"]():
                terminate_process(process)
                raise TaskCancelled("任务已由用户取消")
            if callback:
                # 子进程按固定音频块运行；在无逐词时间戳时提供保守的活动进度。
                callback(min(0.95, (__import__("time").monotonic() - started) / max(30.0, duration * 0.35)))
            __import__("time").sleep(0.25)
        stdout, stderr = process.communicate()
        if process.returncode:
            detail = (stderr or stdout or "Qwen3-ASR 子进程失败").strip().splitlines()[-1]
            raise RuntimeError(f"Qwen3-ASR 失败：{detail[:300]}")
        raw = json.loads(temporary.read_text(encoding="utf-8"))
    finally:
        temporary.unlink(missing_ok=True)
    rows = [{
        "segment_id": f"seg_{index:05d}", "start_seconds": round(float(row["start_seconds"]), 3),
        "end_seconds": round(float(row["end_seconds"]), 3), "text": str(row["text"]).strip(),
        "avg_logprob": 0.0, "no_speech_prob": 0.0,
    } for index, row in enumerate(raw.get("segments", []), start=1) if str(row.get("text", "")).strip()]
    raw_data = {
        "schema_version": 1, "generated_at": now_iso(), "engine": "qwen3-asr-0.6b",
        "model": str(model), "device": raw.get("device"), "compute_type": raw.get("compute_type"),
        "language": raw.get("language", settings.get("language", "zh")), "language_probability": 0.0,
        "initial_prompt": _initial_prompt(settings, context), "timestamp_precision": "chunk",
        "segments": rows, "fallbacks": [],
    }
    emit_runtime_event(
        settings, "asr", "info",
        f"语音识别完成：Qwen3-ASR · {'GPU' if raw.get('device') == 'cuda' else 'CPU'} · {raw.get('compute_type', '')}",
        code="asr_completed", device="gpu" if raw.get("device") == "cuda" else "cpu",
        compute_type=str(raw.get("compute_type", "")), segment_count=len(raw_data["segments"]),
    )
    return raw_data
