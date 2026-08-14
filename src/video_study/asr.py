from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any

from .utils import TaskCancelled, ensure_not_cancelled, now_iso, srt_time, write_json
from .media import prepare_cuda_runtime


def _terminology_rules(settings: dict[str, Any]) -> dict[str, str]:
    raw = settings.get("terminology_replacements", {}) or {}
    if not isinstance(raw, dict):
        raise ValueError("asr.terminology_replacements 必须是“误识别词: 标准术语”的映射")
    rules: dict[str, str] = {}
    for source, target in raw.items():
        if not isinstance(source, str) or not isinstance(target, str):
            raise ValueError("asr.terminology_replacements 的键和值都必须是字符串")
        if source and source != target:
            rules[source] = target
    return rules


def _correct_text(text: str, rules: dict[str, str]) -> tuple[str, list[dict[str, Any]]]:
    if not text or not rules:
        return text, []
    # 单次正则替换可避免 A->B、B->C 产生意外的级联纠错；长词优先匹配。
    pattern = re.compile("|".join(re.escape(item) for item in sorted(rules, key=len, reverse=True)))
    counts: Counter[str] = Counter()

    def replace(match: re.Match[str]) -> str:
        source = match.group(0)
        counts[source] += 1
        return rules[source]

    corrected = pattern.sub(replace, text)
    applied = [
        {"source": source, "target": rules[source], "count": counts[source]}
        for source in sorted(counts, key=lambda item: (-len(item), item))
    ]
    return corrected, applied


def apply_terminology_corrections(data: dict, settings: dict[str, Any]) -> tuple[dict, bool]:
    """应用可逆的字面术语纠错，并保留原始 ASR 文本和时间戳。"""
    result = deepcopy(data)
    rules = _terminology_rules(settings)
    totals: Counter[tuple[str, str]] = Counter()
    corrected_segments = 0
    for row in result.get("segments", []):
        raw_text = str(row.get("raw_text", row.get("text", "")))
        corrected, applied = _correct_text(raw_text, rules)
        row["text"] = corrected
        if applied:
            corrected_segments += 1
            row["raw_text"] = raw_text
            row["terminology_corrections"] = applied
            for item in applied:
                totals[(item["source"], item["target"])] += int(item["count"])
        else:
            # 删除或调整规则后，从 raw_text 恢复，保证无需重跑 ASR。
            row.pop("raw_text", None)
            row.pop("terminology_corrections", None)

    if rules:
        result["terminology_correction"] = {
            "configured_rules": len(rules),
            "corrected_segments": corrected_segments,
            "replacement_count": sum(totals.values()),
            "applied": [
                {"source": source, "target": target, "count": count}
                for (source, target), count in sorted(totals.items())
            ],
        }
    else:
        result.pop("terminology_correction", None)
    return result, result != data


def _write_srt(output_srt: Path, rows: list[dict]) -> None:
    output_srt.parent.mkdir(parents=True, exist_ok=True)
    with output_srt.open("w", encoding="utf-8-sig") as handle:
        for index, row in enumerate(rows, start=1):
            handle.write(f"{index}\n{srt_time(row['start_seconds'])} --> {srt_time(row['end_seconds'])}\n{row['text']}\n\n")


def _initial_prompt(settings: dict[str, Any], context: str | None = None) -> str | None:
    """合并人工热词与视频标题；标题往往包含课程最关键的领域术语。"""
    terms: list[str] = []
    for item in [*settings.get("hotwords", []), context or ""]:
        value = str(item).strip()
        if value and value not in terms:
            terms.append(value)
    return "，".join(terms) or None


def _transcribe_once(
    audio: Path,
    model_path: str,
    settings: dict[str, Any],
    device: str,
    context: str | None = None,
):
    if device == "cuda":
        prepare_cuda_runtime()
    from faster_whisper import WhisperModel

    compute_type = settings.get("compute_type", "int8_float16") if device == "cuda" else "int8"
    model = WhisperModel(model_path, device=device, compute_type=compute_type)
    prompt = _initial_prompt(settings, context)
    segments, info = model.transcribe(
        str(audio),
        language=settings.get("language", "zh"),
        beam_size=int(settings.get("beam_size", 5)),
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
        initial_prompt=prompt,
        word_timestamps=False,
        condition_on_previous_text=True,
    )
    rows = []
    duration = max(1.0, float(settings.get("_duration_seconds", 1.0)))
    callback = settings.get("_progress_callback")
    for segment in segments:
        ensure_not_cancelled(settings.get("_cancel_check"))
        rows.append(segment)
        if callback:
            callback(min(0.99, float(segment.end) / duration))
    return rows, info, compute_type


def transcribe(
    audio: Path,
    output_json: Path,
    output_srt: Path,
    model_dir: Path,
    settings: dict,
    force: bool = False,
    context: str | None = None,
) -> dict:
    engine = str(settings.get("engine", "faster-whisper"))
    if output_json.exists() and not force:
        ensure_not_cancelled(settings.get("_cancel_check"))
        cached = json.loads(output_json.read_text(encoding="utf-8"))
        cached_engine = str(cached.get("engine", "faster-whisper"))
        if cached_engine == engine or bool(settings.get("_preserve_cached_engine")):
            data, changed = apply_terminology_corrections(cached, settings)
            if changed:
                write_json(output_json, data)
            if changed or not output_srt.exists():
                _write_srt(output_srt, data.get("segments", []))
            return data
    if engine == "qwen3-asr-0.6b":
        return _transcribe_qwen(audio, output_json, output_srt, settings, context)
    requested = settings.get("device", "auto")
    device = "cuda" if requested == "auto" else requested
    try:
        segments, info, compute_type = _transcribe_once(audio, str(model_dir), settings, device, context)
    except TaskCancelled:
        raise
    except Exception as exc:
        if device != "cuda":
            raise
        print(f"[ASR] CUDA 不可用，回退 CPU：{exc}")
        device = "cpu"
        segments, info, compute_type = _transcribe_once(audio, str(model_dir), settings, device, context)

    rows = []
    for index, segment in enumerate(segments, start=1):
        text = segment.text.strip()
        if not text:
            continue
        rows.append({
            "segment_id": f"seg_{index:05d}",
            "start_seconds": round(float(segment.start), 3),
            "end_seconds": round(float(segment.end), 3),
            "text": text,
            "avg_logprob": round(float(segment.avg_logprob), 4),
            "no_speech_prob": round(float(segment.no_speech_prob), 4),
        })
    data = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "model": str(model_dir),
        "engine": "faster-whisper",
        "device": device,
        "compute_type": compute_type,
        "language": getattr(info, "language", settings.get("language", "zh")),
        "language_probability": round(float(getattr(info, "language_probability", 0.0)), 4),
        "initial_prompt": _initial_prompt(settings, context),
        "segments": rows,
    }
    data, _ = apply_terminology_corrections(data, settings)
    write_json(output_json, data)
    _write_srt(output_srt, data["segments"])
    return data


def _transcribe_qwen(
    audio: Path, output_json: Path, output_srt: Path, settings: dict[str, Any], context: str | None,
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
    output_json.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, dir=output_json.parent) as handle:
        temporary = Path(handle.name)
    try:
        process = subprocess.Popen([
            str(python), str(runner), "--runtime", str(runtime), "--model", str(model),
            "--audio", str(audio), "--output", str(temporary), "--language", str(settings.get("language", "zh")),
            "--chunk-seconds", str(max(15, int(settings.get("qwen_chunk_seconds", 60)))),
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
        callback = settings.get("_progress_callback")
        duration = max(1.0, float(settings.get("_duration_seconds", 1.0)))
        started = __import__("time").monotonic()
        while process.poll() is None:
            if settings.get("_cancel_check") and settings["_cancel_check"]():
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
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
    data = {
        "schema_version": 1, "generated_at": now_iso(), "engine": "qwen3-asr-0.6b",
        "model": str(model), "device": raw.get("device"), "compute_type": raw.get("compute_type"),
        "language": raw.get("language", settings.get("language", "zh")), "language_probability": 0.0,
        "initial_prompt": _initial_prompt(settings, context), "timestamp_precision": "chunk",
        "segments": rows,
    }
    data, _ = apply_terminology_corrections(data, settings)
    write_json(output_json, data)
    _write_srt(output_srt, data["segments"])
    return data
