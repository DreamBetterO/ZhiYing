from __future__ import annotations

import json
import os
import re
from collections import Counter
from pathlib import Path

from .providers import AllModelsFailed, FallbackChatClient
from .utils import hhmmss, now_iso, write_json

_KNOWLEDGE_GENERATOR_VERSION = 12


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


def _env_int(name: str | None, default: int) -> int:
    value = os.getenv(name or "")
    return int(value) if value and value.isdigit() else int(default)


def _env_bool(name: str | None, default: bool) -> bool:
    value = os.getenv(name or "")
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def build_cloud_source_blocks(transcript: dict) -> tuple[str, dict[str, list[str]]]:
    """压缩口语碎片，并建立稳定的块 ID 到原始 segment ID 映射。"""
    rows = merge_transcript_segments(transcript.get("segments", []), max_chars=180, max_seconds=45.0)
    lines = []
    source_blocks: dict[str, list[str]] = {}
    for index, row in enumerate(rows, start=1):
        block_id = f"block_{index:04d}"
        source_blocks[block_id] = list(row["source_segment_ids"])
        lines.append(
            f"[{block_id}; time:{hhmmss(row['start_seconds'])}-{hhmmss(row['end_seconds'])}] {row['text']}"
        )
    return "\n".join(lines), source_blocks


def build_cloud_source(transcript: dict) -> str:
    """供预算统计和诊断使用的压缩云端输入。"""
    return build_cloud_source_blocks(transcript)[0]


def _validate_qwen_payload(parsed: dict, source_blocks: dict[str, list[str]] | None = None) -> None:
    if len(str(parsed.get("document_title", "")).strip()) < 2:
        raise ValueError("云端结果缺少 document_title")
    if len(str(parsed.get("overview", "")).strip()) < 20:
        raise ValueError("云端结果缺少 overview")
    sections = parsed.get("sections")
    if not isinstance(sections, list) or not sections:
        raise ValueError("云端结果缺少 sections")
    if len(sections) > 16:
        raise ValueError("云端结果章节数量异常")
    for section in sections:
        if not str(section.get("title", "")).strip() or not str(section.get("summary", "")).strip():
            raise ValueError("云端章节缺少标题或摘要")
        points = section.get("knowledge_points")
        if not isinstance(points, list) or not points:
            raise ValueError("云端章节缺少知识点")
        for point in points:
            if not str(point.get("statement", "")).strip():
                raise ValueError("云端知识点缺少陈述")
            if len(str(point.get("explanation", "")).strip()) < 8:
                raise ValueError("云端知识点缺少有效解释")
            block_ids = point.get("source_block_ids")
            if not isinstance(block_ids, list) or not block_ids:
                raise ValueError("云端知识点缺少来源块 ID")
            if source_blocks is not None and any(item not in source_blocks for item in block_ids):
                raise ValueError("云端知识点引用了不存在的来源块 ID")
            for field in ("details", "steps", "examples", "pitfalls", "conditions"):
                value = point.get(field, [])
                if value is not None and not isinstance(value, list):
                    raise ValueError(f"云端知识点字段 {field} 必须是列表")
            if point.get("editorial_note") and len(str(point["editorial_note"]).strip()) < 8:
                raise ValueError("整理说明过短，无法提供有效帮助")
    polished_values = [str(parsed.get("document_title", "")), str(parsed.get("overview", ""))]
    polished_values.extend(_string_list(parsed.get("learning_objectives")))
    for section in sections:
        polished_values.extend((str(section.get("title", "")), str(section.get("summary", ""))))
        for point in section.get("knowledge_points", []):
            polished_values.extend((
                str(point.get("statement", "")), str(point.get("explanation", "")),
                str(point.get("editorial_note", "")), str(point.get("review_tip", "")),
            ))
            for field in ("details", "steps", "examples", "pitfalls", "conditions"):
                polished_values.extend(_string_list(point.get(field)))
    review = parsed.get("review") if isinstance(parsed.get("review"), dict) else {}
    polished_values.append(str(review.get("knowledge_thread", "")))
    polished_values.extend(_string_list(review.get("checklist")))
    polished_values.extend(_string_list(review.get("open_questions")))
    polished_text = "\n".join(polished_values)
    if re.search(r"[（(](?:疑似|应为|可能是|音同|原文不清)[^）)]{0,24}[）)]", polished_text):
        raise ValueError("云端结果含有未清理的括号猜词")


def _qwen_summary(transcript: dict, settings: dict) -> tuple[dict, str, list[dict], dict[str, int]]:
    api_key = settings.get("_runtime_api_key") or os.getenv(settings.get("api_key_env", "QWEN_API_KEY"))
    base_url = settings.get("_runtime_base_url") or os.getenv(
        settings.get("base_url_env", "QWEN_BASE_URL"), settings.get("default_base_url")
    )
    runtime_models = settings.get("_runtime_models")
    chain_value = os.getenv(settings.get("model_chain_env", "QWEN_MODEL_CHAIN"), "")
    models = list(runtime_models or ([item.strip() for item in chain_value.split(",") if item.strip()] or settings.get("default_models", [])))
    budget = settings.get("budget", {})
    max_chars = _env_int(settings.get("max_input_chars_env"), budget.get("max_input_chars", 8000))
    max_tokens = _env_int(settings.get("max_output_tokens_env"), budget.get("max_output_tokens", 2500))
    max_calls = int(settings.get("_runtime_max_calls") or _env_int(
        settings.get("max_calls_env"), budget.get("max_calls_per_video", 1)
    ))
    if max_calls < 1:
        raise RuntimeError("云端调用预算为 0")
    models = models[:max_calls]
    source, source_blocks = build_cloud_source_blocks(transcript)
    if len(source) > max_chars:
        raise RuntimeError(f"转写共 {len(source)} 字符，超过云端预算上限 {max_chars}；未发送请求")
    level, profile = _content_profile(settings)
    target_points = max(
        int(profile["target_min"]),
        min(int(profile["target_max"]), max(1, len(source) // int(profile["target_divisor"]))),
    )
    prompt = f"""你是专业的课程讲义编辑。请把口语 ASR 转写整理成学完课程后可直接复习的中文讲义，而不是只有几条结论的摘要。
当前内容档位：{level}（{profile['label']}）。{profile['points']}；{profile['detail']}。根据有效内容密度自然组织，全文可参考约 {target_points} 个知识点，但不得为凑数量重复或虚构。
输出必须在 {max_tokens} Token 上限内完整结束；若内容丰富度与长度冲突，优先减少次要细节，确保 JSON 语法完整合法，绝不能输出被截断的 JSON。
只允许依据给定转写，不补充外部知识。你可以把来源中分散但明确的内容整理成步骤、对比和逻辑关系，并放入 editorial_note，作为“整理说明”；整理说明不得引入来源没有的新事实。ASR 可能有同音字、断句和无意义乱码：仅在上下文高度明确时直接改成正确表达；无法确定时省略该噪声，若它影响核心结论则明确写“此处转写不清，需回看原视频”。不要保留“错词（猜测正确词）”这种括号猜词形式。
删除寒暄、重复、自我宣传、课程销售、互动口令、泛泛鼓励和无信息量口头语。一个知识点必须包含具体定义、关系、原因、步骤、条件或明确结论；不要把“努力学习”“课程内容全新”等课程元信息单独列为知识点。
严格区分“讲者提出/承诺后续解决的问题”和“本段已经给出的答案”。如果转写只说课程将解决某个问题，就写成学习目标，不得声称资料已经分析或解决该问题。标题和总览只能概括本段实际讲清的内容；除非来源确实给出了分析或方案，否则标题不得使用“解析”“解决方案”“完整指南”等过度承诺词。
不要制造循环论证，例如把“未实现目标”本身写成“未实现目标的原因”。来源没有说出具体原因或障碍时，应明确写“本段提出了该问题，但尚未给出具体原因”，不要自行归因，也不要把不同时间段的观点擅自拼成因果关系。
按内容主题而不是固定时间切章；章节标题应概括内容，不要写时间戳。知识点解释必须让未观看视频的读者理解讲者在说什么，不能只换一种说法重复标题。来源包含步骤、案例、条件、边界或易错点时，必须放入对应字段；没有则输出空数组。不要把同一案例拆成多条重复知识点。
严格控制结构长度：每个知识点的 details、examples、conditions、pitfalls 各最多 2 条，steps 最多 5 条；每条只表达一个信息。explanation 应完整但避免重复这些列表，editorial_note 和 review_tip 各最多一句。
learning_objectives 只写本视频实际涉及的学习目标。review.knowledge_thread 概括知识之间的先后关系；review.checklist 用可快速复习的短句列出关键规则；review.open_questions 只记录讲者提出但本段未回答、或转写确实不清的问题。
输出一个 JSON 对象，格式严格如下：
{{"document_title":"根据内容拟定的资料标题，不照抄无意义文件名","overview":"用 2-4 句话说明视频主题、内容主线和读者能获得什么","learning_objectives":["学习目标"],"sections":[{{"title":"章节标题","summary":"本节摘要","knowledge_points":[{{"statement":"简洁的知识点标题或结论","explanation":"忠于转写的完整解释","details":["补充细节或推导"],"steps":["操作或判断步骤"],"examples":["讲者给出的案例"],"conditions":["适用条件或边界"],"pitfalls":["易错点或易混淆点"],"editorial_note":"仅用于来源内的逻辑整理，没有则为空字符串","review_tip":"一句话复习提示","source_block_ids":["block_0001"]}}]}}],"review":{{"knowledge_thread":"本课知识主线","checklist":["关键规则"],"open_questions":["尚未讲清或需要回看的问题"]}}}}
每个知识点必须引用至少一个方括号中真实存在的 block_id；可以引用多个内容块，不要编造未显示的 ID。不要输出 source_segment_ids，由系统根据 block_id 展开。
不确定内容明确写“不确定”；不要在标题、正文或 JSON 字段中生成时间戳。
务必输出 JSON，不要输出 JSON 之外的文字。
输出前在内部逐句检查：是否有直接来源支持、是否把课程承诺写成既成结论、是否残留 ASR 错词或括号猜词、是否存在空洞因果。发现任一问题就改写或删除；不要输出检查过程。

转写：
{source}"""
    client = FallbackChatClient(
        api_key=api_key or "", base_url=base_url or "", models=models,
        timeout=float(settings.get("timeout_seconds", 90.0)),
    )
    parsed, model, attempts, usage = client.create_json(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
        max_tokens=max_tokens,
        validator=lambda payload: _validate_qwen_payload(payload, source_blocks),
        on_attempt=settings.get("_runtime_attempt_callback"),
    )
    _validate_qwen_payload(parsed, source_blocks)
    for section in parsed.get("sections", []):
        for point in section.get("knowledge_points", []):
            segment_ids = []
            for block_id in point.pop("source_block_ids", []):
                for segment_id in source_blocks[block_id]:
                    if segment_id not in segment_ids:
                        segment_ids.append(segment_id)
            point["source_segment_ids"] = segment_ids
    usage["source_chars"] = len(source)
    return parsed, model, [item.__dict__ for item in attempts], usage


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


def build_document(
    manifest: dict,
    transcript: dict,
    frames: dict,
    output: Path,
    qwen_settings: dict,
    render_settings: dict,
    force: bool = False,
    cloud_override: bool | None = None,
) -> dict:
    render_options = {
        "include_full_transcript": bool(render_settings.get("include_full_transcript", True)),
        "offline_section_seconds": max(60, int(render_settings.get("offline_section_seconds", 300))),
        "offline_points_per_section": max(1, int(render_settings.get("offline_points_per_section", 3))),
        "source_link_base": str(render_settings.get("source_link_base", "video-study://play")).rstrip("/"),
        "content_level": str(render_settings.get("content_level", qwen_settings.get("content_level", "推荐"))),
    }
    configured_default = bool(qwen_settings.get("enabled", False))
    use_qwen = cloud_override if cloud_override is not None else _env_bool(qwen_settings.get("enabled_env"), configured_default)
    expected_mode = (
        "cloud_summary" if use_qwen and transcript.get("segments")
        else "offline_extract" if transcript.get("segments")
        else "no_speech"
    )
    if output.exists() and not force:
        cached = json.loads(output.read_text(encoding="utf-8"))
        if (
            cached.get("generator_version") == _KNOWLEDGE_GENERATOR_VERSION
            and cached.get("transcript") == transcript.get("segments", [])
            and cached.get("figures") == frames.get("frames", [])
            and cached.get("render_options") == render_options
            and cached.get("mode") == expected_mode
        ):
            return cached
        print("[knowledge] 转写、截图或离线整理逻辑已更新，重新生成知识文档（不重跑 ASR）")
    if use_qwen and transcript.get("segments"):
        try:
            cloud_settings = dict(qwen_settings)
            cloud_settings["content_level"] = render_options["content_level"]
            summary, model, attempts, usage = _qwen_summary(transcript, cloud_settings)
            document = _normalize_document(
                manifest, transcript, frames, summary.get("sections", []),
                render_settings.get("source_link_base", "video-study://play"),
                "cloud_summary", str(summary.get("overview", "")).strip(),
                _string_list(summary.get("learning_objectives")), summary.get("review", {}),
            )
            if not document["sections"]:
                raise ValueError("云端知识点未能匹配真实来源 ID")
            document["model"] = model
            document["model_attempts"] = attempts
            document["cloud_usage"] = usage
            document["metadata"]["document_title"] = str(summary.get("document_title", "")).strip()
        except (AllModelsFailed, RuntimeError, ValueError) as exc:
            print(f"[LLM] 云端总结跳过：{exc}")
            document = _fallback_document(
                manifest, transcript, frames, render_settings,
                reason="智能整理未完成；当前产物为离线提取预览，完整转写仍保存在 JSON/SRT 中。",
            )
            document["cloud_failure"] = type(exc).__name__
    else:
        document = _fallback_document(manifest, transcript, frames, render_settings)
    document["generator_version"] = _KNOWLEDGE_GENERATOR_VERSION
    document["render_options"] = render_options
    write_json(output, document)
    return document
