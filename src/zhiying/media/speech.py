from __future__ import annotations

import json
import multiprocessing
import os
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .transcript import (
    apply_terminology_corrections,
    clamp_segment_timestamps,
    normalize_transcript,
    write_srt,
)
from ..utils import (
    TaskCancelled, background_process_kwargs, emit_runtime_event, ensure_not_cancelled,
    now_iso, terminate_process, write_json,
)
from .processing import prepare_cuda_runtime


def _to_serializable(value: Any) -> Any:
    """深度转换为可 pickle 的普通 Python 类型（MappingProxyType → dict 等）。"""
    if isinstance(value, Mapping):
        return {str(key): _to_serializable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_serializable(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [_to_serializable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def asr_runtime_limit_seconds(settings: dict[str, Any], duration_seconds: float) -> float:
    """为本地 ASR 子进程提供有限且随视频时长扩展的总运行边界。"""
    configured = settings.get("max_runtime_seconds")
    if configured is not None:
        value = float(configured)
        if value <= 0:
            raise ValueError("asr.max_runtime_seconds 必须大于 0")
        return value
    ratio = max(2.0, float(settings.get("max_runtime_ratio", 2.5)))
    return max(600.0, max(1.0, float(duration_seconds)) * ratio + 300.0)


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
    config_root: str = "",
) -> None:
    """隔离 faster-whisper 推理，使父进程可立即终止长时间首段推理。"""
    try:
        if device == "cuda":
            extra_dirs: list[Path] = []
            if config_root:
                runtime_torch_lib = Path(config_root) / "models" / "qwen3-asr-runtime" / "torch" / "lib"
                if runtime_torch_lib.is_dir():
                    extra_dirs.append(runtime_torch_lib)
            prepare_cuda_runtime(extra_dirs=extra_dirs)
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
    config_root = str(settings.get("_config_root", ""))
    worker_settings = _to_serializable({
        key: value for key, value in settings.items()
        if not key.startswith("_") and not callable(value)
    })
    worker_settings["duration_seconds"] = max(1.0, float(settings.get("_duration_seconds", 1.0)))
    receive, send = multiprocessing.get_context("spawn").Pipe(duplex=False)
    try:
        process = multiprocessing.get_context("spawn").Process(
            target=_faster_whisper_worker,
            args=(send, str(audio), model_path, worker_settings, device, context, config_root),
            daemon=True,
            name="zhiying-faster-whisper",
        )
        process.start()
    except Exception as exc:
        send.close()
        receive.close()
        raise RuntimeError(
            f"worker 启动失败（参数序列化）：{type(exc).__name__}: {exc}"
        ) from exc
    send.close()
    callback = settings.get("_progress_callback")
    final: dict[str, Any] | None = None
    started_at = time.monotonic()
    runtime_limit = asr_runtime_limit_seconds(settings, worker_settings["duration_seconds"])
    try:
        while process.is_alive() or receive.poll():
            if settings.get("_cancel_check") and settings["_cancel_check"]():
                _stop_worker(process)
                raise TaskCancelled("任务已由用户取消")
            if time.monotonic() - started_at >= runtime_limit:
                _stop_worker(process)
                raise TimeoutError(f"faster-whisper 超过运行上限（{int(runtime_limit)} 秒）")
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
    attempt_id = f"fw-{int(time.monotonic() * 1000)}"
    emit_runtime_event(
        settings, "asr", "info",
        f"faster-whisper 正在隔离进程中加载模型并尝试 {'GPU' if device == 'cuda' else 'CPU'}",
        code="asr_attempt_started", attempt_id=attempt_id,
        engine="faster-whisper", requested_device=device,
    )
    try:
        segments, info, compute_type = _transcribe_once(audio, str(model_dir), settings, device, context)
    except TaskCancelled:
        raise
    except Exception as exc:
        error_text = str(exc)
        is_worker_error = "参数序列化" in error_text or "pickle" in error_text.lower()
        if device != "cuda" or is_worker_error:
            emit_runtime_event(
                settings, "asr", "error",
                f"faster-whisper 失败：{type(exc).__name__}: {exc}",
                code="asr_attempt_failed", attempt_id=attempt_id,
                engine="faster-whisper", error_type=type(exc).__name__,
            )
            raise
        fallback_message = f"GPU 识别不可用，已降级到 CPU：{type(exc).__name__}: {exc}"
        fallbacks.append({"code": "asr_cuda_fallback", "message": fallback_message})
        emit_runtime_event(
            settings, "asr", "warning", fallback_message,
            code="asr_attempt_degraded", attempt_id=attempt_id,
            engine="faster-whisper", failed_device="cuda", next_device="cpu",
            error_type=type(exc).__name__,
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
        code="asr_attempt_succeeded", attempt_id=attempt_id, engine="faster-whisper",
        device="gpu" if device == "cuda" else "cpu",
        compute_type=compute_type, segment_count=len(raw["segments"]),
        degraded=bool(fallbacks),
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
    runner = resolve_setting(settings.get("qwen_runner", "scripts/workers/qwen_asr_runner.py"))
    for path, label in ((python, "Qwen ASR Python"), (runtime, "Qwen ASR 运行库"), (model, "Qwen ASR 模型"), (runner, "Qwen ASR runner")):
        if not path.exists():
            raise FileNotFoundError(f"{label}不存在：{path}")
    temporary_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, dir=temporary_dir) as handle:
        temporary = Path(handle.name)
    process: subprocess.Popen[str] | None = None
    drain_threads: list[threading.Thread] = []
    attempt_id = f"qwen-{int(time.monotonic() * 1000)}"
    emit_runtime_event(
        settings, "asr", "info",
        "Qwen3-ASR 正在隔离进程中加载模型；可随时取消",
        code="asr_attempt_started", attempt_id=attempt_id,
        engine="qwen3-asr-0.6b", requested_device="auto",
    )
    try:
        process = subprocess.Popen([
            str(python), str(runner), "--runtime", str(runtime), "--model", str(model),
            "--audio", str(audio), "--output", str(temporary), "--language", str(settings.get("language", "zh")),
            "--chunk-seconds", str(max(15, int(settings.get("qwen_chunk_seconds", 60)))),
            "--max-rtf", str(float(settings.get("qwen_max_rtf", 0.75))),
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"}, **background_process_kwargs())
        callback = settings.get("_progress_callback")
        duration = max(1.0, float(settings.get("_duration_seconds", 1.0)))
        started = time.monotonic()
        runtime_limit = asr_runtime_limit_seconds(settings, duration)
        stdout_lines: deque[str] = deque(maxlen=256)
        stderr_lines: deque[str] = deque(maxlen=256)
        total_chunks = 0
        completed_chunks = 0
        model_loaded = False
        zero_sent = False
        failure_detail = ""

        def drain(stream, target: deque[str]) -> None:
            if stream is None:
                return
            for line in iter(stream.readline, ""):
                target.append(str(line))

        def handle_stdout_event(line: str) -> None:
            nonlocal completed_chunks, total_chunks, model_loaded, failure_detail
            try:
                evt = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                return
            evt_type = str(evt.get("type", ""))
            if evt_type == "environment":
                emit_runtime_event(
                    settings, "asr", "info",
                    f"Qwen 运行环境：{evt.get('device', 'cpu')}"
                    f"{' · ' + evt.get('gpu_name', '') if evt.get('gpu_name') else ''}"
                    f"{' · VRAM ' + str(evt.get('free_vram_mb', 0)) + 'MB' if evt.get('device') == 'cuda' else ''}",
                    code="asr_environment", attempt_id=attempt_id,
                    device=evt.get("device"), gpu=evt.get("gpu_name", ""),
                    vram_mb=evt.get("free_vram_mb", 0),
                )
            elif evt_type == "load_started":
                emit_runtime_event(
                    settings, "asr", "info",
                    f"Qwen 模型加载中（{evt.get('device', 'cpu')} · {evt.get('dtype', '')}）",
                    code="asr_model_load_started", attempt_id=attempt_id,
                    device=evt.get("device"), dtype=evt.get("dtype"),
                )
            elif evt_type == "load_completed":
                model_loaded = True
                emit_runtime_event(
                    settings, "asr", "info",
                    f"Qwen 模型加载完成：{evt.get('backend', '')} · {evt.get('load_seconds', 0):.1f}s",
                    code="asr_model_load_completed", attempt_id=attempt_id,
                    device=evt.get("device"), backend=evt.get("backend"),
                    load_seconds=evt.get("load_seconds"),
                )
            elif evt_type == "load_failed":
                failure_detail = str(evt.get("error") or "Qwen3-ASR 模型加载失败")
                missing = evt.get("missing")
                if isinstance(missing, list) and missing:
                    failure_detail = "缺少 Qwen3-ASR 官方运行依赖：" + ", ".join(str(item) for item in missing)
                emit_runtime_event(
                    settings, "asr", "error", failure_detail,
                    code="asr_attempt_failed", attempt_id=attempt_id,
                    engine="qwen3-asr-0.6b", reason=evt.get("reason", "load_failed"),
                    error_type=evt.get("error_type"), missing=missing,
                )
            elif evt_type == "chunk_completed":
                completed_chunks = int(evt.get("chunk_index", 0))
                total_chunks = int(evt.get("chunk_total", 0))
                audio_done = float(evt.get("audio_seconds", 0))
                if callback:
                    fraction = min(0.99, audio_done / max(1.0, float(evt.get("total_audio_seconds", duration))))
                    callback(fraction)
                emit_runtime_event(
                    settings, "asr", "info",
                    f"Qwen 识别进度：{completed_chunks}/{total_chunks} 段",
                    code="asr_chunk_completed", attempt_id=attempt_id,
                    chunk_index=completed_chunks, chunk_total=total_chunks,
                    rtf=evt.get("rtf"), batch_size=evt.get("batch_size"),
                )
            elif evt_type == "speed_circuit_breaker":
                warning_detail = f"Qwen3-ASR 速度低于建议阈值（RTF={evt.get('avg_rtf', 0):.3f}），将继续使用已完成结果"
                emit_runtime_event(
                    settings, "asr", "warning", warning_detail,
                    code="asr_speed_warning", attempt_id=attempt_id,
                    engine="qwen3-asr-0.6b", reason="speed_warning",
                    avg_rtf=evt.get("avg_rtf"), threshold=evt.get("threshold"),
                )
            elif evt_type == "oom_fallback":
                emit_runtime_event(
                    settings, "asr", "warning",
                    f"Qwen 显存不足，批量从 {evt.get('batch_size', 0)} 降至 {evt.get('new_batch_size', 1)}",
                    code="asr_oom_fallback", attempt_id=attempt_id,
                    batch_size=evt.get("batch_size"), new_batch_size=evt.get("new_batch_size"),
                )
            elif evt_type == "inference_error":
                failure_detail = f"Qwen 推理错误：{evt.get('error_type', '')}: {evt.get('error', '')}"
                emit_runtime_event(
                    settings, "asr", "error", failure_detail,
                    code="asr_attempt_failed", attempt_id=attempt_id,
                    engine="qwen3-asr-0.6b", error_type=evt.get("error_type"),
                )
            elif evt_type == "final":
                emit_runtime_event(
                    settings, "asr", "info",
                    f"Qwen 识别完成：{evt.get('segment_count', 0)} 个片段 · {evt.get('backend', '')}",
                    code="asr_attempt_succeeded", attempt_id=attempt_id,
                    engine="qwen3-asr-0.6b", device=evt.get("device"),
                    backend=evt.get("backend"), segment_count=evt.get("segment_count"),
                )

        stdout_thread = threading.Thread(target=drain, args=(process.stdout, stdout_lines), daemon=True)
        stderr_thread = threading.Thread(target=drain, args=(process.stderr, stderr_lines), daemon=True)
        stdout_thread.start()
        stderr_thread.start()
        drain_threads = [stdout_thread, stderr_thread]
        while process.poll() is None:
            if settings.get("_cancel_check") and settings["_cancel_check"]():
                terminate_process(process)
                emit_runtime_event(
                    settings, "asr", "info", "Qwen3-ASR 已取消",
                    code="asr_attempt_cancelled", attempt_id=attempt_id, engine="qwen3-asr-0.6b",
                )
                raise TaskCancelled("任务已由用户取消")
            while stdout_lines:
                handle_stdout_event(stdout_lines.popleft())
            if not model_loaded and callback and not zero_sent:
                zero_sent = True
                callback(0.0)
            if time.monotonic() - started >= runtime_limit:
                terminate_process(process)
                raise TimeoutError(f"Qwen3-ASR 超过运行上限（{int(runtime_limit)} 秒）")
            time.sleep(0.1)
        stdout_thread.join(timeout=1.0)
        stderr_thread.join(timeout=1.0)
        while stdout_lines:
            handle_stdout_event(stdout_lines.popleft())
        stderr = "".join(stderr_lines)
        if process.returncode:
            detail = failure_detail
            if not detail:
                detail = (stderr or "Qwen3-ASR 子进程失败").strip().splitlines()[-1] if stderr.strip() else "Qwen3-ASR 子进程失败"
            emit_runtime_event(
                settings, "asr", "error",
                f"Qwen3-ASR 失败：{detail[:300]}",
                code="asr_attempt_failed", attempt_id=attempt_id,
                exit_code=process.returncode,
            )
            raise RuntimeError(f"Qwen3-ASR 失败：{detail[:300]}")
        raw = json.loads(temporary.read_text(encoding="utf-8"))
    finally:
        if process is not None and process.poll() is None:
            terminate_process(process)
        for thread in drain_threads:
            thread.join(timeout=1.0)
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
        code="asr_attempt_succeeded", attempt_id=attempt_id, engine="qwen3-asr-0.6b",
        device="gpu" if raw.get("device") == "cuda" else "cpu",
        compute_type=str(raw.get("compute_type", "")), segment_count=len(raw_data["segments"]),
    )
    return raw_data
