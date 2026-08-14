from __future__ import annotations

import math
import re
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from .utils import srt_time


NORMALIZATION_VERSION = 1


def terminology_rules(settings: Mapping[str, Any]) -> dict[str, str]:
    raw = settings.get("terminology_replacements", {}) or {}
    if not isinstance(raw, Mapping):
        raise ValueError("asr.terminology_replacements 必须是“误识别词: 标准术语”的映射")
    rules: dict[str, str] = {}
    for source, target in raw.items():
        if not isinstance(source, str) or not isinstance(target, str):
            raise ValueError("asr.terminology_replacements 的键和值都必须是字符串")
        if source and source != target:
            rules[source] = target
    return rules


def _correct_text(text: str, rules: Mapping[str, str]) -> tuple[str, list[dict[str, Any]]]:
    if not text or not rules:
        return text, []
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


def apply_terminology_corrections(
    data: Mapping[str, Any], settings: Mapping[str, Any],
) -> tuple[dict[str, Any], bool]:
    """纯函数：从原始文本应用非级联纠错，并保留逐段追溯信息。"""
    result = deepcopy(dict(data))
    rules = terminology_rules(settings)
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


def clamp_segment_timestamps(
    data: Mapping[str, Any], duration_seconds: float,
) -> tuple[dict[str, Any], bool]:
    """纯函数：将时间戳限制在真实视频时长，保留尾段与稳定 segment_id。"""
    try:
        duration = float(duration_seconds)
    except (TypeError, ValueError):
        return deepcopy(dict(data)), False
    if not math.isfinite(duration) or duration <= 0:
        return deepcopy(dict(data)), False
    result = deepcopy(dict(data))
    for row in result.get("segments", []):
        try:
            start = float(row.get("start_seconds", 0.0))
            end = float(row.get("end_seconds", start))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(start) or not math.isfinite(end):
            continue
        start = round(max(0.0, min(duration, start)), 3)
        end = round(max(start, min(duration, end)), 3)
        row["start_seconds"] = start
        row["end_seconds"] = end
    return result, result != data


def normalize_transcript(
    raw: Mapping[str, Any], settings: Mapping[str, Any], duration_seconds: float,
) -> dict[str, Any]:
    clamped, _ = clamp_segment_timestamps(raw, duration_seconds)
    normalized, _ = apply_terminology_corrections(clamped, settings)
    normalized["normalization"] = {
        "version": NORMALIZATION_VERSION,
        "timestamp_rule": "video-duration-clamp-v1",
        "terminology_rules": sorted(terminology_rules(settings).items()),
    }
    normalized.pop("runtime", None)
    return normalized


def write_srt(output: Path, rows: list[dict[str, Any]]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig") as handle:
        for index, row in enumerate(rows, start=1):
            handle.write(
                f"{index}\n{srt_time(row['start_seconds'])} --> "
                f"{srt_time(row['end_seconds'])}\n{row['text']}\n\n"
            )


def join_segment_text(rows: list[dict[str, Any]]) -> str:
    result = ""
    for row in rows:
        value = str(row.get("text", "")).strip()
        if not value:
            continue
        if result and result[-1] not in "，。！？；：,.!?;:" and value[0] not in "，。！？；：,.!?;:":
            result += "，"
        result += value
    return result


def merge_transcript_segments(
    segments: list[dict[str, Any]], max_chars: int = 96, max_seconds: float = 24.0,
) -> list[dict[str, Any]]:
    """把短 ASR 片段合为可读段落，同时保留完整来源 ID。"""
    merged: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []

    def flush() -> None:
        if current:
            merged.append({
                "text": join_segment_text(current),
                "source_segment_ids": [row["segment_id"] for row in current],
                "start_seconds": float(current[0]["start_seconds"]),
                "end_seconds": float(current[-1]["end_seconds"]),
            })

    for segment in segments:
        if not str(segment.get("text", "")).strip():
            continue
        proposed = [*current, segment]
        gap = float(segment["start_seconds"]) - float(current[-1]["end_seconds"]) if current else 0.0
        duration = float(segment["end_seconds"]) - float(current[0]["start_seconds"]) if current else 0.0
        if current and (gap > 1.5 or duration > max_seconds or len(join_segment_text(proposed)) > max_chars):
            flush()
            current = []
        current.append(segment)
    flush()
    return merged
