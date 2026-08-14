from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from ..utils import hhmmss, now_iso

_KNOWLEDGE_GENERATOR_VERSION = 15


def _source_rows_unchanged(cached_rows: object, source_rows: object) -> bool:
    """比较源字段，允许文档包含 width/height 等渲染派生字段。"""
    if not isinstance(cached_rows, list) or not isinstance(source_rows, list):
        return False
    if len(cached_rows) != len(source_rows):
        return False
    return all(
        isinstance(cached, dict)
        and isinstance(source, dict)
        and all(cached.get(key) == value for key, value in source.items())
        for cached, source in zip(cached_rows, source_rows)
    )


_CONTENT_PROFILES = {
    "精简": {
        "label": "复习提纲",
        "points": "每章通常 1–3 个知识点",
        "detail": "只保留最关键的定义、规则和结论，解释简洁但完整",
        "target_divisor": 2400,
        "target_min": 4,
        "target_max": 12,
    },
    "推荐": {
        "label": "标准课程笔记",
        "points": "每章通常 2–6 个知识点",
        "detail": "保留必要解释、步骤、代表性案例、适用条件和易错点",
        "target_divisor": 2200,
        "target_min": 8,
        "target_max": 18,
    },
    "丰富": {
        "label": "完整课程讲义",
        "points": "每章通常 2–6 个知识点",
        "detail": "尽量保留推导过程、操作步骤、多个案例、边界条件、易错点和复习提示",
        "target_divisor": 4000,
        "target_min": 8,
        "target_max": 10,
    },
}


def _string_list(value: object, limit: int = 12) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()][:limit]


def _content_profile(settings: dict) -> tuple[str, dict]:
    level = str(settings.get("content_level", "推荐"))
    if level not in _CONTENT_PROFILES:
        level = "推荐"
    return level, _CONTENT_PROFILES[level]


def _joined_text(rows: list[dict]) -> str:
    result = ""
    for row in rows:
        text = str(row.get("text", "")).strip()
        if not text:
            continue
        if result and result[-1] not in "，。！？；：,.!?;:" and text[0] not in "，。！？；：,.!?;:":
            result += "，"
        result += text
    return result


def merge_transcript_segments(
    segments: list[dict], max_chars: int = 96, max_seconds: float = 24.0,
) -> list[dict]:
    """把口语 ASR 的一两秒碎片合成可读段落，同时保留全部来源 ID。"""
    merged: list[dict] = []
    current: list[dict] = []

    def flush() -> None:
        if not current:
            return
        merged.append({
            "text": _joined_text(current),
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
        if current and (gap > 1.5 or duration > max_seconds or len(_joined_text(proposed)) > max_chars):
            flush()
            current = []
        current.append(segment)
    flush()
    return merged


_GENERIC_BIGRAMS = {
    "同学", "我们", "那么", "这个", "就是", "今天", "课程", "非常", "如果", "一个",
    "很多", "的话", "因为", "所以", "什么", "没有", "还是", "可以", "应该", "现在",
    "时候", "进行", "大家", "这里", "然后", "部分", "对于", "来看", "好吧", "相信",
}


def _bigrams(text: str) -> set[str]:
    compact = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", text)
    return {compact[index:index + 2] for index in range(max(0, len(compact) - 1))}


def _keyword_counts(rows: list[dict]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for row in rows:
        counts.update(item for item in _bigrams(row["text"]) if item not in _GENERIC_BIGRAMS)
    return counts


def _informativeness(text: str, keyword_counts: Counter[str]) -> float:
    compact = re.sub(r"\s+", "", text)
    score = min(len(compact), 90) / 24
    for cue in ("是", "包括", "分为", "核心", "重点", "原因", "目的", "因为", "所以", "需要", "可以", "意味着", "第一", "第二"):
        if cue in compact:
            score += 0.55
    for cue in ("主要包含", "主要是包含", "分为", "整堂课的重点", "核心是", "核心为", "是一个", "目的是", "意味着"):
        if cue in compact:
            score += 1.8
    if "任何一种方法" in compact or re.search(r"目的[^，。]{0,10}(?:是|应该)", compact):
        score += 2.0
    for filler in ("同学们", "好吧", "非常好", "我相信", "有没有", "感谢", "打个一", "OK", "缘分"):
        if filler in compact:
            score -= 0.9
    repeated_terms = sum(
        min(keyword_counts[item] - 1, 3)
        for item in _bigrams(compact)
        if keyword_counts[item] > 1 and item not in _GENERIC_BIGRAMS
    )
    score += repeated_terms * 0.12
    if re.search(r"什么是|是什么|如何|为什么", compact) and not any(
        cue in compact for cue in ("就是", "是一个", "核心为", "指的是", "整堂课的重点")
    ):
        score -= 2.4
    if len(compact) < 14:
        score -= 1.5
    return score


def _section_topic(rows: list[dict], selected: list[dict]) -> str:
    text = "。".join(row["text"] for row in rows)
    selected_text = "。".join(row["text"] for row in selected)
    if "整堂课的重点" in selected_text:
        return "课程重点与学习安排"
    if "中枢为核心" in selected_text:
        match = re.search(r"([\u4e00-\u9fff]{2,8})的核心", text)
        if match:
            topic = re.sub(r"^(?:就是整个|这是整个|整个|就是|这是)", "", match.group(1))
            return f"{topic}的核心定义"
        return "核心概念与定义"
    for pattern in (
        r"什么是[^，。！？]{1,14}",
        r"如何[^，。！？]{2,14}",
        r"为什么[^，。！？]{2,14}",
        r"[^，。！？]{2,14}(?:核心|重点|原因|目的)",
    ):
        match = re.search(pattern, text)
        if match:
            topic = re.sub(r"(?:同学们|同学|大家)$", "", match.group(0).strip("，。！？；： "))
            return topic[:24]
    return "课程要点"


def _offline_sections(segments: list[dict], section_seconds: int, max_points: int) -> list[dict]:
    merged = merge_transcript_segments(segments)
    groups: dict[int, list[dict]] = {}
    for row in merged:
        groups.setdefault(int(row["start_seconds"] // section_seconds), []).append(row)
    sections = []
    for rows in groups.values():
        keywords = _keyword_counts(rows)
        ranked = sorted(
            rows,
            key=lambda row: (_informativeness(row["text"], keywords), len(row["text"])),
            reverse=True,
        )
        selected_ranked: list[dict] = []
        for row in ranked:
            row_terms = _bigrams(row["text"])
            repeated_row_terms = {
                item for item in row_terms
                if keywords[item] > 1 and item not in _GENERIC_BIGRAMS
            }
            if any(
                len(repeated_row_terms & {
                    item for item in _bigrams(existing["text"])
                    if keywords[item] > 1 and item not in _GENERIC_BIGRAMS
                }) / max(1, min(
                    len(repeated_row_terms),
                    len({
                        item for item in _bigrams(existing["text"])
                        if keywords[item] > 1 and item not in _GENERIC_BIGRAMS
                    }),
                )) > 0.40
                for existing in selected_ranked
            ):
                continue
            selected_ranked.append(row)
            if len(selected_ranked) >= max(1, max_points):
                break
        selected = sorted(selected_ranked, key=lambda row: row["start_seconds"])
        if not selected:
            continue
        start = rows[0]["start_seconds"]
        end = rows[-1]["end_seconds"]
        sections.append({
            "title": f"{hhmmss(start)}–{hhmmss(end)} · {_section_topic(rows, selected)}",
            "summary": f"本节从 {len(rows)} 个合并转写段中离线筛选代表性内容，未补充转写之外的知识。",
            "knowledge_points": [{
                "statement": row["text"],
                "explanation": "",
                "source_segment_ids": row["source_segment_ids"],
            } for row in selected],
        })
    return sections


def _fallback_document(
    manifest: dict,
    transcript: dict,
    frames: dict,
    render_settings: dict,
    reason: str | None = None,
) -> dict:
    segments = transcript["segments"]
    section_seconds = max(60, int(render_settings.get("offline_section_seconds", 300)))
    max_points = max(1, int(render_settings.get("offline_points_per_section", 3)))
    sections = _offline_sections(segments, section_seconds, max_points)
    no_speech = not segments
    return _normalize_document(
        manifest,
        transcript,
        frames,
        sections,
        render_settings.get("source_link_base", "video-study://play"),
        "no_speech" if no_speech else "offline_extract",
        "未检测到可转写人声；当前文档保留视频信息和关键画面，未生成虚构转写。"
        if no_speech else
        reason or "云端总结未启用；已在本地合并碎片转写并筛选代表性要点，不补充外部知识。",
    )


def _point_distance(timestamp: float, point: dict) -> float:
    if point["start_seconds"] <= timestamp <= point["end_seconds"]:
        return 0.0
    return min(abs(timestamp - point["start_seconds"]), abs(timestamp - point["end_seconds"]))


def _normalize_document(
    manifest: dict,
    transcript: dict,
    frames: dict,
    raw_sections: list[dict],
    source_link_base: str,
    mode: str,
    overview: str,
    learning_objectives: list[str] | None = None,
    review: dict | None = None,
) -> dict:
    from urllib.parse import quote
    segment_map = {row["segment_id"]: row for row in transcript["segments"]}
    normalized_sections = []
    for section in raw_sections:
        points = []
        for point in section.get("knowledge_points", []):
            ids = [item for item in point.get("source_segment_ids", []) if item in segment_map]
            if not ids:
                continue
            refs = [segment_map[item] for item in ids]
            start = min(item["start_seconds"] for item in refs)
            end = max(item["end_seconds"] for item in refs)
            encoded_id = quote(str(manifest["video_id"]), safe="")
            url = f"{source_link_base}/{encoded_id}?t={int(start)}"
            points.append({
                "statement": str(point.get("statement", "")).strip(),
                "explanation": str(point.get("explanation", "")).strip(),
                "details": _string_list(point.get("details")),
                "steps": _string_list(point.get("steps")),
                "examples": _string_list(point.get("examples")),
                "conditions": _string_list(point.get("conditions")),
                "pitfalls": _string_list(point.get("pitfalls")),
                "editorial_note": str(point.get("editorial_note", "")).strip(),
                "review_tip": str(point.get("review_tip", "")).strip(),
                "source_segment_ids": ids,
                "start_seconds": start,
                "end_seconds": end,
                "source_label": f"{hhmmss(start)}–{hhmmss(end)}",
                "source_url": url,
                "figures": [],
            })
        if points:
            section_start = min(point["start_seconds"] for point in points)
            section_end = max(point["end_seconds"] for point in points)
            normalized_sections.append({
                "title": str(section.get("title", "未命名章节")),
                "summary": str(section.get("summary", "")),
                "start_seconds": section_start,
                "end_seconds": section_end,
                "knowledge_points": points,
                "figures": [],
            })
    figure_rows = frames.get("frames", [])
    for figure in figure_rows:
        if not normalized_sections:
            break
        timestamp = float(figure.get("timestamp_seconds", 0.0))
        if float(manifest.get("duration_seconds", 0.0)) >= 1800.0 and timestamp < 60.0:
            # 长课程开头一分钟常是桌面、播放器或等待画面；即使语音已开始，也不作为知识点配图。
            continue
        targets = [
            (section, point)
            for section in normalized_sections
            for point in section["knowledge_points"]
        ]
        target_section, target_point = min(targets, key=lambda row: _point_distance(timestamp, row[1]))
        related = dict(figure)
        related["caption"] = (
            f"与“{target_point['statement']}”讲解同期的画面（{figure.get('timestamp_label', hhmmss(timestamp))}）"
        )
        related["related_point"] = target_point["statement"]
        target_point["figures"].append(related)
        target_section["figures"].append(related)
    review = review if isinstance(review, dict) else {}
    used_segment_ids = {
        segment_id
        for section in normalized_sections
        for point in section["knowledge_points"]
        for segment_id in point["source_segment_ids"]
    }
    transcript_ids = {str(row.get("segment_id", "")) for row in transcript.get("segments", [])}
    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "mode": mode,
        "metadata": {
            "video_id": manifest["video_id"],
            "title": manifest["title"],
            "source_video": manifest["source_path"],
            "duration_seconds": manifest["duration_seconds"],
            "duration_label": hhmmss(manifest["duration_seconds"]),
        },
        "overview": overview,
        "learning_objectives": _string_list(learning_objectives),
        "sections": normalized_sections,
        "figures": figure_rows,
        "transcript": transcript["segments"],
        "notice": "本资料由自动程序依据视频转写整理，请结合原视频核对重要信息。",
        "review": {
            "knowledge_thread": str(review.get("knowledge_thread", "")).strip(),
            "checklist": _string_list(review.get("checklist"), 20),
            "open_questions": _string_list(review.get("open_questions"), 20),
        },
        "quality": {
            "section_count": len(normalized_sections),
            "knowledge_point_count": sum(len(section["knowledge_points"]) for section in normalized_sections),
            "source_segment_coverage": round(len(used_segment_ids) / max(1, len(transcript_ids)), 4),
            "figure_count": len(figure_rows),
            "figures_linked_to_points": sum(
                len(point["figures"])
                for section in normalized_sections
                for point in section["knowledge_points"]
            ),
        },
    }
