"""Adapter：把 KnowledgeUnit 列表转换为现有 document.json 格式。

V2 变更：
- 删除无条件最近时间挂图（_link_figures），改用显式 VisualBinding
- 支持 content_blocks 块级渲染，同时生成旧字段供向后兼容
- 图片等比缩放，读取真实宽高
- 图注使用 reader_focus
"""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any
from urllib.parse import quote

from .document import _string_list
from ..utils import hhmmss, now_iso
from .schema import KnowledgeUnit, LessonPlan, VisualBinding, FrameSemantic, VisualEvidence

# 知识类型 → 渲染时使用的字段映射
_TYPE_FIELD_MAP: dict[str, list[str]] = {
    "concept": ["prerequisites", "branches", "pitfalls"],
    "rule": ["rules", "branches", "exceptions", "positive_examples", "negative_examples", "pitfalls"],
    "procedure": ["procedure", "exceptions", "pitfalls"],
    "mechanism": ["branches", "rules", "pitfalls"],
    "comparison": ["branches", "rules", "positive_examples"],
    "case": ["procedure", "positive_examples", "pitfalls"],
    "boundary_case": ["negative_examples", "pitfalls"],
    "visual_or_formula": ["branches", "procedure", "unresolved"],
    "conclusion": ["prerequisites"],
}


def _flatten_field(value: Any) -> list[str]:
    """把字段值统一转为字符串列表。"""
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            if isinstance(item, str):
                result.append(item)
            elif isinstance(item, dict):
                parts = [f"{k}: {v}" for k, v in item.items() if v]
                if parts:
                    result.append("；".join(parts))
            else:
                result.append(str(item))
        return [s.strip() for s in result if s.strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _image_dimensions(path: str) -> tuple[int, int]:
    """使用 Pillow 读取图片真实宽高。"""
    try:
        from PIL import Image
        with Image.open(path) as img:
            return img.size
    except Exception:
        return 1280, 720


def _reader_open_questions(value: object) -> list[str]:
    rows = _string_list(value, 20)
    internal = re.compile(
        r"(?:chapter_\d+|云端请求预算|请求预算已用尽|connection error|AllModelsFailed|模型均不可用)",
        flags=re.IGNORECASE,
    )
    return [row for row in rows if not internal.search(row)]


def _blocks_to_legacy_fields(blocks: list[dict[str, Any]]) -> dict[str, list[str]]:
    """从 content_blocks 生成 details/steps/examples/conditions/pitfalls。"""
    result: dict[str, list[str]] = {
        "details": [], "steps": [], "examples": [], "conditions": [], "pitfalls": [],
    }
    for block in blocks:
        btype = block.get("type", "")
        if btype == "rule_list":
            result["details"].extend(block.get("items", []))
        elif btype == "steps":
            result["steps"].extend(block.get("items", []))
        elif btype == "example":
            result["examples"].extend(block.get("items", []))
        elif btype == "pitfall":
            result["pitfalls"].extend(block.get("items", []))
        elif btype == "understanding_tip":
            result["pitfalls"].append(block.get("text", ""))
    return result


def _normalize_content_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize common model field variants before all renderers consume them."""
    normalized: list[dict[str, Any]] = []
    list_types = {"rule_list", "steps", "example", "pitfall"}
    text_types = {"paragraph", "visual_lead_in", "figure_caption", "visual_takeaway", "understanding_tip"}
    passthrough_types = {"figure", "visual_group", "source_links"}
    for raw in blocks:
        if not isinstance(raw, dict):
            continue
        block = dict(raw)
        btype = str(block.get("type", "")).strip()
        text = str(block.get("text", "") or "").strip()
        items = _flatten_field(block.get("items", []))
        if btype in list_types:
            if not items and text:
                items = [text]
            if not items:
                continue
            block["items"] = items
            block.pop("text", None)
        elif btype in text_types:
            if not text and items:
                text = "；".join(items)
            if not text:
                continue
            block["text"] = text
            block.pop("items", None)
        elif btype not in passthrough_types:
            if not text and items:
                text = "；".join(items)
            if not text:
                continue
            block["type"] = "paragraph"
            block["text"] = text
            block.pop("items", None)
        normalized.append(block)
    return normalized


_LEGACY_BODY_FIELDS = (
    "explanation", "details", "steps", "examples", "conditions",
    "pitfalls", "editorial_note", "review_tip",
)


def _canonical_blocks_from_point(point: dict[str, Any]) -> list[dict[str, Any]]:
    blocks = _normalize_content_blocks(list(point.get("content_blocks", [])))
    had_canonical_blocks = bool(blocks)
    segment_ids = [str(item) for item in point.get("source_segment_ids", []) if str(item)]
    seen = {
        re.sub(r"\s+", "", str(text)).strip("，。；; ")
        for block in blocks
        for text in ([block.get("text", "")] if block.get("text") else block.get("items", []))
        if str(text).strip()
    }

    def add(block_type: str, values: list[str], origin: str = "audio_backed") -> None:
        unique: list[str] = []
        for value in values:
            text = str(value).strip()
            normalized = re.sub(r"\s+", "", text).strip("，。；; ")
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            unique.append(text)
        if not unique:
            return
        block = {
            "block_id": f"migration_b{len(blocks) + 1:03d}",
            "type": block_type,
            "origin": origin,
            "source_ids": list(segment_ids) if origin == "audio_backed" else [],
            "claim_ids": [],
        }
        if block_type in {"rule_list", "steps", "example", "pitfall"}:
            block["items"] = unique
        else:
            block["text"] = "。".join(item.rstrip("。") for item in unique) + "。"
        blocks.append(block)

    if not had_canonical_blocks:
        add("paragraph", [str(point.get("explanation", ""))])
        add("rule_list", _flatten_field(point.get("details")))
        add("steps", _flatten_field(point.get("steps")))
        add("example", _flatten_field(point.get("examples")))
        add("rule_list", _flatten_field(point.get("conditions")))
        add("pitfall", _flatten_field(point.get("pitfalls")))
        add("understanding_tip", [str(point.get("review_tip", ""))], origin="model_aid")
        if not blocks:
            add("paragraph", [str(point.get("statement", ""))])
    for block in blocks:
        if block.get("origin") == "audio_backed" and not block.get("source_ids"):
            block["source_ids"] = list(segment_ids)
    return blocks


def v1_to_v2(document: dict[str, Any]) -> dict[str, Any]:
    """Read-only migration to canonical blocks/source_refs; never mutates input."""
    migrated = deepcopy(document)
    for section in migrated.get("sections", []):
        for point in section.get("knowledge_points", []):
            point["content_blocks"] = _canonical_blocks_from_point(point)
            point["source_refs"] = {
                "segment_ids": [str(item) for item in point.get("source_segment_ids", []) if str(item)],
                "start_seconds": float(point.get("start_seconds", 0.0) or 0.0),
                "end_seconds": float(point.get("end_seconds", 0.0) or 0.0),
                "label": str(point.get("source_label", "")),
                "url": str(point.get("source_url", "")),
                "links": [dict(item) for item in point.get("source_links", []) if isinstance(item, dict)],
            }
            for field in (*_LEGACY_BODY_FIELDS, "source_segment_ids", "start_seconds", "end_seconds", "source_label", "source_url", "source_links"):
                point.pop(field, None)
    migrated["schema_version"] = 2
    return migrated


def _unit_to_point(
    unit: KnowledgeUnit,
    segment_map: dict[str, dict],
    manifest: dict,
    source_link_base: str,
) -> dict[str, Any] | None:
    """把单个 KnowledgeUnit 转换为 document.json 的 knowledge_point。"""
    # 收集所有 segment IDs
    segment_ids: list[str] = []
    for ref in unit.evidence_refs:
        for sid in ref.get("segment_ids", []):
            if sid in segment_map and sid not in segment_ids:
                segment_ids.append(sid)
    if not segment_ids:
        return None

    refs = [segment_map[sid] for sid in segment_ids]
    start = min(r["start_seconds"] for r in refs)
    end = max(r["end_seconds"] for r in refs)
    encoded_id = quote(str(manifest["video_id"]), safe="")
    url = f"{source_link_base}/{encoded_id}?t={int(start)}"

    content_blocks = _normalize_content_blocks(unit.content_blocks)
    # 如果有 content_blocks，从中生成旧字段
    if content_blocks:
        legacy = _blocks_to_legacy_fields(content_blocks)
        details = legacy["details"]
        steps = legacy["steps"]
        examples = legacy["examples"]
        conditions = legacy["conditions"]
        pitfalls = legacy["pitfalls"]
    else:
        # 旧路径：根据知识类型选择字段
        field_order = _TYPE_FIELD_MAP.get(unit.type, _TYPE_FIELD_MAP["concept"])
        details = []
        steps = []
        examples = []
        conditions = []
        pitfalls = []
        for field_name in field_order:
            values = _flatten_field(getattr(unit, field_name, []))
            if not values:
                continue
            if field_name in ("procedure",):
                steps.extend(values)
            elif field_name in ("positive_examples", "negative_examples"):
                examples.extend(values)
            elif field_name in ("exceptions", "unresolved"):
                conditions.extend(values)
            elif field_name in ("pitfalls",):
                pitfalls.extend(values)
            else:
                details.extend(values)

    # 未解决项放入 editorial_note
    editorial_parts: list[str] = []
    if unit.unresolved:
        editorial_parts.extend(unit.unresolved)

    point = {
        "statement": unit.title,
        "explanation": unit.definition_or_conclusion or unit.title,
        "details": _string_list(details),
        "steps": _string_list(steps),
        "examples": _string_list(examples),
        "conditions": _string_list(conditions),
        "pitfalls": _string_list(pitfalls),
        "editorial_note": "；".join(editorial_parts) if editorial_parts else "",
        "review_tip": "",
        "source_segment_ids": segment_ids,
        "start_seconds": start,
        "end_seconds": end,
        "source_label": f"{hhmmss(start)}–{hhmmss(end)}",
        "source_url": url,
        "figures": [],
        # V2 新增字段
        "plan_id": unit.plan_id,
        "detail_level": unit.detail_level,
        "facet_status": dict(unit.facet_status),
        "content_blocks": content_blocks,
        "visual_evidence": list(unit.visual_evidence) if unit.visual_evidence else [],
    }
    return point


def _group_units_to_sections(
    units: list[KnowledgeUnit],
    segment_map: dict[str, dict],
    manifest: dict,
    source_link_base: str,
    lesson_plan: LessonPlan | None = None,
) -> list[dict[str, Any]]:
    """把 KnowledgeUnit 列表按时间顺序或 lesson_plan 章节分组。"""
    points_with_time: list[tuple[float, float, dict[str, Any], str]] = []
    for unit in units:
        point = _unit_to_point(unit, segment_map, manifest, source_link_base)
        if point is None:
            continue
        points_with_time.append((point["start_seconds"], point["end_seconds"], point, unit.type))

    if not points_with_time:
        return []

    if lesson_plan and lesson_plan.chapters:
        by_plan_id = {point.get("plan_id", ""): (start, end, point, ktype) for start, end, point, ktype in points_with_time}
        planned_sections: list[dict[str, Any]] = []
        for chapter in lesson_plan.chapters:
            rows = [by_plan_id[up.plan_id] for up in chapter.unit_plans if up.plan_id in by_plan_id]
            if not rows:
                continue
            section_start = min(row[0] for row in rows)
            section_end = max(row[1] for row in rows)
            planned_sections.append({
                "title": chapter.title or f"章节 {len(planned_sections) + 1}",
                "summary": "",
                "start_seconds": section_start,
                "end_seconds": section_end,
                "knowledge_points": [row[2] for row in rows],
                "figures": [],
            })
        if planned_sections:
            return planned_sections

    # 按开始时间排序
    points_with_time.sort(key=lambda x: x[0])

    # 按时间间隙分章（间隙 > 120 秒新建章节）
    sections: list[dict[str, Any]] = []
    current_points: list[dict[str, Any]] = []
    current_types: list[str] = []
    section_start = points_with_time[0][0]
    section_end = points_with_time[0][1]

    type_labels = {
        "concept": "概念", "rule": "规则", "procedure": "流程",
        "mechanism": "原理", "comparison": "对比", "case": "案例",
        "boundary_case": "边界", "visual_or_formula": "图表",
        "conclusion": "结论",
    }

    for start, end, point, ktype in points_with_time:
        if current_points and start - section_end > 120.0:
            sections.append({
                "title": f"{type_labels.get(current_types[0], '知识')} · {hhmmss(section_start)}–{hhmmss(section_end)}",
                "summary": "",
                "start_seconds": section_start,
                "end_seconds": section_end,
                "knowledge_points": current_points,
                "figures": [],
            })
            current_points = []
            current_types = []
            section_start = start
        current_points.append(point)
        current_types.append(ktype)
        section_end = max(section_end, end)

    if current_points:
        sections.append({
            "title": f"{type_labels.get(current_types[0], '知识')} · {hhmmss(section_start)}–{hhmmss(section_end)}",
            "summary": "",
            "start_seconds": section_start,
            "end_seconds": section_end,
            "knowledge_points": current_points,
            "figures": [],
        })

    return sections


def _apply_visual_bindings(
    sections: list[dict[str, Any]],
    units: list[KnowledgeUnit],
    frames: dict,
    bindings: list[VisualBinding],
) -> None:
    """根据显式绑定关联图片到知识点。

    只处理 decision == "bind" 的绑定。
    图片插入到 target_block_id 指定的位置（如果有 content_blocks），
    否则放在知识点末尾。
    caption 使用 reader_focus。
    """
    frame_map = {f.get("image_id", ""): f for f in frames.get("frames", [])}

    # 构建 unit_id → (section, point) 映射
    point_map: dict[str, tuple[dict, dict]] = {}
    for section in sections:
        for point in section["knowledge_points"]:
            # 匹配 unit_id 或 plan_id
            point_map[point.get("plan_id", "")] = (section, point)

    for binding in bindings:
        if binding.decision != "bind":
            continue
        frame = frame_map.get(binding.frame_id)
        if not frame:
            continue

        target = point_map.get(binding.unit_id)
        if not target:
            continue

        section, point = target
        related = dict(frame)
        # 图注使用 reader_focus
        if binding.reader_focus:
            related["caption"] = f"观察重点：{binding.reader_focus}"
        else:
            related["caption"] = f"视频关键画面（{frame.get('timestamp_label', hhmmss(float(frame.get('timestamp_seconds', 0))))}）"
        related["reader_focus"] = binding.reader_focus
        related["binding_id"] = binding.frame_id
        related["relation"] = binding.relation
        related["related_point"] = point["statement"]

        # 读取图片真实宽高
        w, h = _image_dimensions(frame.get("path", ""))
        related["width"] = w
        related["height"] = h

        point["figures"].append(related)
        section["figures"].append(related)


def _apply_visual_evidence(
    sections: list[dict[str, Any]],
    evidence: list[VisualEvidence],
    manifest: dict,
    source_link_base: str,
) -> None:
    """把已仲裁且有像素证据的 VisualEvidence 转成知识点局部 figure。"""
    point_map: dict[str, tuple[dict, dict]] = {}
    for section in sections:
        for point in section["knowledge_points"]:
            if point.get("plan_id"):
                point_map[point["plan_id"]] = (section, point)

    used_scenes: set[str] = set()
    encoded_id = quote(str(manifest["video_id"]), safe="")
    for item in evidence:
        if item.decision != "select" or not item.image_path:
            continue
        if not item.visible_evidence and not item.ocr_text:
            continue
        target_id = item.primary_unit_id or item.matched_knowledge_id or item.matched_knowledge_point_id
        target = point_map.get(target_id)
        if not target:
            continue
        scene_id = item.scene_cluster_id or item.dedup_group_id or item.image_sha256 or item.image_path
        if scene_id in used_scenes:
            continue
        section, point = target
        timestamp = item.source_timestamp or item.timestamp
        timestamp_label = hhmmss(timestamp)
        related = {
            "image_id": item.frame_id or item.evidence_id,
            "timestamp_seconds": timestamp,
            "timestamp_label": timestamp_label,
            "source_timestamp": timestamp,
            "source_url": f"{source_link_base}/{encoded_id}?t={int(timestamp)}",
            "path": item.image_path,
            "caption": item.suggested_caption or f"视觉证据（{timestamp_label}）",
            "reader_focus": item.why_useful,
            "binding_id": item.evidence_id,
            "evidence_id": item.evidence_id,
            "question_id": item.question_id,
            "relation": "visual_evidence",
            "related_point": point["statement"],
            "visual_summary": item.visual_summary,
            "why_useful": item.why_useful,
            "match_reason": item.match_reason,
            "explanation_for_reader": item.explanation_for_reader,
            "dedup_group_id": item.dedup_group_id,
            "scene_cluster_id": item.scene_cluster_id,
            "visible_evidence": list(item.visible_evidence),
            "visual_role": item.visual_role,
            "criteria_met": list(item.criteria_met),
            "visual_answer": item.visual_answer,
            "sequence_mode": item.sequence_mode,
            "visual_group_id": item.visual_group_id,
        }
        w, h = _image_dimensions(item.image_path)
        related["width"] = w
        related["height"] = h
        point["figures"].append(related)
        section["figures"].append(related)
        used_scenes.add(scene_id)


_VISUAL_BLOCK_TYPES = {
    "visual_lead_in", "figure", "figure_caption", "visual_takeaway", "visual_group",
}


def _visual_group_block(items: list[VisualEvidence]) -> dict[str, Any]:
    item = items[0]
    timestamp = item.source_timestamp or item.timestamp
    binding_ids = [row.evidence_id for row in items]
    captions = [row.suggested_caption for row in items if row.suggested_caption]
    takeaways = list(dict.fromkeys(
        row.explanation_for_reader or row.visual_summary
        for row in items
        if row.explanation_for_reader or row.visual_summary
    ))
    lead_in = f"看图重点：{item.why_useful}"
    caption = captions[0] if len(captions) == 1 else ""
    takeaway = "；".join(takeaways)
    # 确定性完全重复折叠：lead_in、caption 和 takeaway 完全相同时只保留一次
    if caption and caption == lead_in:
        caption = ""
    if takeaway and takeaway == lead_in:
        takeaway = ""
    if takeaway and takeaway == caption:
        takeaway = ""
    return {
        "block_id": f"{item.visual_group_id or item.evidence_id}_group",
        "type": "visual_group",
        "origin": "visual_backed",
        "binding_id": item.evidence_id,
        "binding_ids": binding_ids,
        "lead_in": lead_in,
        "caption": caption,
        "takeaway": takeaway,
        "source_timestamp": timestamp,
        "source_label": hhmmss(timestamp),
        "scene_cluster_id": item.scene_cluster_id or item.dedup_group_id,
        "match_reason": item.match_reason,
        "visual_role": item.visual_role,
        "sequence_mode": item.sequence_mode,
        "visual_group_id": item.visual_group_id or item.evidence_id,
        "criteria_met": list(dict.fromkeys(
            criterion for row in items for criterion in row.criteria_met
        )),
    }


def _normalize_visual_groups(
    sections: list[dict[str, Any]],
    evidence: list[VisualEvidence],
) -> None:
    """删除 writer 自建图片块，只保留证据 ID 驱动的原子 visual_group。"""
    by_unit: dict[str, list[VisualEvidence]] = {}
    for item in evidence:
        if item.decision != "select" or not item.image_path:
            continue
        if not item.visible_evidence and not item.ocr_text:
            continue
        unit_id = item.primary_unit_id or item.matched_knowledge_id or item.matched_knowledge_point_id
        by_unit.setdefault(unit_id, []).append(item)

    for section in sections:
        for point in section.get("knowledge_points", []):
            unit_evidence = by_unit.get(str(point.get("plan_id", "")), [])
            valid_ids = {item.evidence_id for item in unit_evidence}
            groups: dict[str, list[VisualEvidence]] = {}
            for item in unit_evidence:
                group_id = (
                    item.visual_group_id
                    if item.sequence_mode != "single" and item.visual_group_id
                    else item.evidence_id
                )
                groups.setdefault(group_id, []).append(item)
            group_blocks = [_visual_group_block(items) for items in groups.values()]
            inserted = False
            normalized: list[dict[str, Any]] = []
            for block in point.get("content_blocks", []) or []:
                block_type = str(block.get("type", ""))
                if block_type not in _VISUAL_BLOCK_TYPES:
                    normalized.append(block)
                    continue
                binding_id = str(block.get("binding_id", ""))
                binding_ids = {str(value) for value in (block.get("binding_ids") or [])}
                if not inserted and (binding_id in valid_ids or binding_ids & valid_ids):
                    normalized.extend(group_blocks)
                    inserted = True
            if not inserted:
                normalized.extend(group_blocks)
            point["content_blocks"] = normalized


def units_to_document(
    units: list[KnowledgeUnit],
    manifest: dict,
    transcript: dict,
    frames: dict,
    source_link_base: str,
    cloud_info: dict[str, Any] | None = None,
    selfcheck_report: dict[str, Any] | None = None,
    lesson_plan: LessonPlan | None = None,
    visual_bindings: list[VisualBinding] | None = None,
    frame_semantics: list[FrameSemantic] | None = None,
    visual_evidence: list[VisualEvidence] | None = None,
) -> dict[str, Any]:
    """把 KnowledgeUnit 列表转换为现有 document.json 格式。"""
    segment_map = {row["segment_id"]: row for row in transcript["segments"]}
    sections = _group_units_to_sections(units, segment_map, manifest, source_link_base, lesson_plan)

    # VisualEvidence 是整任务级唯一图片权威；仅在任务未启用它时兼容旧绑定。
    visual_evidence_authoritative = visual_evidence is not None
    if visual_evidence_authoritative:
        _apply_visual_evidence(sections, visual_evidence or [], manifest, source_link_base)
        _normalize_visual_groups(sections, visual_evidence or [])
    elif visual_bindings:
        _apply_visual_bindings(sections, units, frames, visual_bindings)

    cloud_info = cloud_info or {}
    # 文档需要宽高等派生字段，但不能污染 frames 输入；否则下次缓存比较会误判变化。
    figure_rows = [dict(figure) for figure in frames.get("frames", [])]
    # 为所有 frame 添加宽高信息
    for figure in figure_rows:
        if "width" not in figure or "height" not in figure:
            w, h = _image_dimensions(figure.get("path", ""))
            figure["width"] = w
            figure["height"] = h

    used_segment_ids = {
        sid
        for section in sections
        for point in section["knowledge_points"]
        for sid in point["source_segment_ids"]
    }
    transcript_ids = {str(row.get("segment_id", "")) for row in transcript.get("segments", [])}

    review = cloud_info.get("review", {}) if isinstance(cloud_info.get("review"), dict) else {}

    document = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "mode": "cloud_summary" if cloud_info else "offline_extract",
        "metadata": {
            "video_id": manifest["video_id"],
            "title": manifest["title"],
            "document_title": cloud_info.get("document_title", "") or manifest["title"],
            "source_video": manifest["source_path"],
            "duration_seconds": manifest["duration_seconds"],
            "duration_label": hhmmss(manifest["duration_seconds"]),
        },
        "overview": cloud_info.get("overview", "") or "本资料由知识整理流水线自动生成，请结合原视频核对重要信息。",
        "learning_objectives": _string_list(cloud_info.get("learning_objectives")),
        "sections": sections,
        "figures": figure_rows,
        "transcript": transcript["segments"],
        "notice": "本资料由自动程序依据视频转写整理，请结合原视频核对重要信息。理解提示用于辅助学习，不代表讲者原话。",
        "review": {
            "knowledge_thread": str(review.get("knowledge_thread", "")).strip(),
            "checklist": _string_list(review.get("checklist"), 20),
            "open_questions": _reader_open_questions(review.get("open_questions")),
        },
        "quality": {
            "section_count": len(sections),
            "knowledge_point_count": sum(len(s["knowledge_points"]) for s in sections),
            "source_segment_coverage": round(len(used_segment_ids) / max(1, len(transcript_ids)), 4),
            "figure_count": len(figure_rows),
            "figures_linked_to_points": sum(
                len(p["figures"]) for s in sections for p in s["knowledge_points"]
            ),
            "visual_group_count": sum(
                1
                for section in sections
                for point in section["knowledge_points"]
                for block in point.get("content_blocks", [])
                if block.get("type") == "visual_group"
            ),
        },
        "visual_evidence": [item.to_dict() for item in (visual_evidence or [])],
        "visual_source": "visual_evidence" if visual_evidence_authoritative else "legacy_visual_bindings",
        "visual_source_version": "3.0" if visual_evidence_authoritative else "legacy",
    }

    if cloud_info.get("model"):
        document["model"] = cloud_info["model"]
        document["model_attempts"] = cloud_info.get("attempts", [])
        document["cloud_usage"] = cloud_info.get("usage", {})
        document["cloud_stages"] = cloud_info.get("stages", {})
        document["cloud_budget"] = cloud_info.get("request_budget", {})

    if selfcheck_report:
        document["selfcheck"] = selfcheck_report

    return v1_to_v2(document)
