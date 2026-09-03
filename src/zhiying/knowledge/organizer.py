"""按知识类型专业化整理：把写作计划转化为结构化知识单元。

支持离线构造和云端精炼两种模式。V2 消费 LessonPlan 的 unit_plans，
输出 plan_id / detail_level / facet_status / content_blocks / visual_bindings。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

from ..media.transcript import merge_transcript_segments
from .content_profile import CONTENT_PROFILES as _CONTENT_PROFILES, content_profile as _content_profile
from .editorial import EditorialBrief, EditorialDecision
from ..utils import TaskCancelled, write_json
from .schema import (
    ContentDecision,
    CourseProfile,
    FrameSemantic,
    KnowledgeUnit,
    LessonPlan,
    UnitPlan,
    VisualBinding,
    VisualEvidence,
    VisualNeed,
)
from .prompts import compose_course_ir_prompt

_ORGANIZER_VERSION = 12


def _has_plan_coverage(lesson_plan: LessonPlan, units: list[KnowledgeUnit]) -> bool:
    expected_ids = {up.plan_id for up in lesson_plan.all_unit_plans if up.plan_id}
    if not expected_ids:
        return bool(units)
    unit_ids = {unit.plan_id for unit in units if unit.plan_id}
    return unit_ids == expected_ids and len(units) == len(expected_ids)


def _validate_organizer_payload(
    parsed: dict[str, Any],
    expected_plan_ids: set[str],
    source_blocks: dict[str, list[str]],
    source_ids_by_plan: Mapping[str, list[str]] | None = None,
    claim_ids_by_plan: Mapping[str, list[str]] | None = None,
    expected_plan_order: list[str] | None = None,
) -> None:
    sections = parsed.get("sections")
    if not isinstance(sections, list) or not sections:
        raise ValueError("云端整理缺少 sections")
    points: list[dict[str, Any]] = []
    for section in sections:
        if not isinstance(section, dict) or not str(section.get("title", "")).strip():
            raise ValueError("云端整理章节缺少标题")
        rows = section.get("knowledge_points")
        if not isinstance(rows, list) or not rows:
            raise ValueError("云端整理章节缺少 knowledge_points")
        points.extend(row for row in rows if isinstance(row, dict))
    plan_ids = [str(point.get("plan_id", "")).strip() for point in points]
    if expected_plan_order and (
        len(plan_ids) != len(expected_plan_ids) or set(plan_ids) != expected_plan_ids
    ):
        seen: set[str] = set()
        accepted_points: list[dict[str, Any]] = []
        repair_points: list[dict[str, Any]] = []
        for point, plan_id in zip(points, plan_ids):
            if plan_id in expected_plan_ids and plan_id not in seen:
                seen.add(plan_id)
                accepted_points.append(point)
            else:
                repair_points.append(point)
        missing = [plan_id for plan_id in expected_plan_order if plan_id not in seen]
        if len(repair_points) >= len(missing):
            repaired_points = repair_points[:len(missing)]
            for point, plan_id in zip(repaired_points, missing):
                point["plan_id"] = plan_id
            accepted_points.extend(repaired_points)
            accepted_identity = {id(point) for point in accepted_points}
            normalized_sections: list[dict[str, Any]] = []
            for section in sections:
                rows = section.get("knowledge_points", []) if isinstance(section, dict) else []
                kept = [row for row in rows if isinstance(row, dict) and id(row) in accepted_identity]
                if kept:
                    section["knowledge_points"] = kept
                    normalized_sections.append(section)
            parsed["sections"] = normalized_sections
            points = [
                row
                for section in normalized_sections
                for row in section["knowledge_points"]
            ]
            plan_ids = [str(point.get("plan_id", "")).strip() for point in points]
    if len(plan_ids) != len(expected_plan_ids) or set(plan_ids) != expected_plan_ids:
        raise ValueError(
            "云端整理未逐项覆盖写作计划，或包含重复/额外 plan_id："
            f"expected={sorted(expected_plan_ids)}, actual={plan_ids}"
        )
    valid_blocks = set(source_blocks)
    for point in points:
        plan_id = str(point.get("plan_id", "")).strip()
        if claim_ids_by_plan is not None:
            _repair_claim_references(point, plan_id, claim_ids_by_plan.get(plan_id, []))
        if source_ids_by_plan is not None:
            _repair_source_references(point, source_ids_by_plan.get(plan_id, []))
        if len(str(point.get("statement", "")).strip()) < 2:
            raise ValueError(f"云端整理 {plan_id} 缺少知识点标题")
        if len(str(point.get("explanation", "")).strip()) < 4:
            raise ValueError(f"云端整理 {plan_id} 缺少有效解释")
        refs = point.get("source_block_ids")
        if (not isinstance(refs, list) or not refs) and source_ids_by_plan is not None:
            canonical_refs = [
                str(ref).strip()
                for ref in source_ids_by_plan.get(plan_id, [])
                if str(ref).strip() in valid_blocks
            ]
            if canonical_refs:
                point["source_block_ids"] = list(dict.fromkeys(canonical_refs))
                refs = point["source_block_ids"]
        if not isinstance(refs, list) or not refs:
            raise ValueError(f"云端整理 {plan_id} 缺少来源块")
        if any(str(ref).strip() not in valid_blocks for ref in refs):
            raise ValueError(f"云端整理 {plan_id} 引用了不存在的来源块")
        content_blocks = point.get("content_blocks")
        if not isinstance(content_blocks, list) or not content_blocks:
            raise ValueError(f"云端整理 {plan_id} 缺少 content_blocks")
        if any(not isinstance(block, dict) for block in content_blocks):
            raise ValueError(f"云端整理 {plan_id} 的 content_blocks 必须全部为对象")
        for block in content_blocks:
            text = re.sub(r"\s+", "", str(block.get("text", ""))).strip("，。；;：:、")
            raw_items = block.get("items", [])
            if text and isinstance(raw_items, list):
                block["items"] = [
                    item for item in raw_items
                    if re.sub(r"\s+", "", str(item)).strip("，。；;：:、") != text
                ]
        visual_bindings = point.get("visual_bindings", [])
        if not isinstance(visual_bindings, list) or any(not isinstance(item, dict) for item in visual_bindings):
            raise ValueError(f"云端整理 {plan_id} 的 visual_bindings 必须为对象列表")
        facet_status = point.get("facet_status", {})
        if not isinstance(facet_status, dict):
            raise ValueError(f"云端整理 {plan_id} 的 facet_status 必须为对象")
        if not any(
            isinstance(block, dict)
            and (
                str(block.get("text", "") or "").strip()
                or any(str(item).strip() for item in (block.get("items") or []))
                or (
                    block.get("type") == "visual_group"
                    and str(block.get("binding_id", "") or "").strip()
                )
            )
            for block in content_blocks
        ):
            raise ValueError(f"云端整理 {plan_id} 的 content_blocks 没有可渲染内容")


def _repair_claim_references(value: Any, plan_id: str, canonical_ids: list[str]) -> None:
    """修复模型保留语义序号但改写哈希的 claim 引用；无法对应的引用直接移除。"""
    canonical = list(dict.fromkeys(str(item).strip() for item in canonical_ids if str(item).strip()))
    canonical_set = set(canonical)
    prefix = f"claim_{plan_id}_"
    by_ordinal = {
        claim_id[len(prefix):].split("_", 1)[0]: claim_id
        for claim_id in canonical
        if claim_id.startswith(prefix) and "_" in claim_id[len(prefix):]
    }

    def repair(identifier: Any) -> str:
        raw = str(identifier or "").strip()
        if raw in canonical_set:
            return raw
        if raw.startswith(prefix):
            return by_ordinal.get(raw[len(prefix):].split("_", 1)[0], "")
        return ""

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, item in list(node.items()):
                if key == "claim_ids" and isinstance(item, list):
                    node[key] = list(dict.fromkeys(filter(None, (repair(row) for row in item))))
                    continue
                if key == "claim_id":
                    fixed = repair(item)
                    if fixed:
                        node[key] = fixed
                    else:
                        node.pop(key, None)
                    continue
                walk(item)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(value)


def _repair_source_references(value: Any, canonical_ids: list[str]) -> None:
    """把模型漂移的来源标签收敛到当前知识点已授权的真实来源。"""
    canonical = list(dict.fromkeys(str(item).strip() for item in canonical_ids if str(item).strip()))
    canonical_set = set(canonical)

    def repair(rows: Any) -> list[str]:
        if not isinstance(rows, list):
            return []
        normalized = list(dict.fromkeys(
            str(item).strip() for item in rows
            if str(item).strip() in canonical_set
        ))
        # 只有模型确实给了引用、但引用全部漂移时才回填；保留原本有意为空的 model_aid 块。
        return normalized or (list(canonical) if rows else [])

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, item in list(node.items()):
                if key in {"source_block_ids", "source_ids"}:
                    node[key] = repair(item)
                    continue
                walk(item)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(value)


def _segment_text_map(transcript: dict) -> dict[str, str]:
    """构建 segment_id → text 映射。"""
    return {row["segment_id"]: row["text"] for row in transcript.get("segments", [])}


def _split_sentences(text: str, limit: int = 4) -> list[str]:
    parts = [p.strip(" ，。；;") for p in re.split(r"[。；;]", text) if p.strip(" ，。；;")]
    return parts[:limit]


_OFFLINE_FILLER = re.compile(
    r"^(?:嗯+|啊+|呃+|好(?:的)?|那么|然后|就是|这个|那个|对吧|是不是|看到没有|同学们)$"
)


def _clean_offline_fragments(texts: list[str], max_chars: int) -> str:
    """只合并标点、去纯口头填充和相邻重复，不改写转写中的事实。"""
    fragments: list[str] = []
    for raw in texts:
        text = re.sub(r"\s+", "", str(raw)).strip("，。；;！？!? ")
        if len(text) <= 1 or _OFFLINE_FILLER.fullmatch(text):
            continue
        if fragments and text == fragments[-1]:
            continue
        fragments.append(text)
    sentences: list[str] = []
    current = ""
    for fragment in fragments:
        separator = "，" if current else ""
        if current and len(current) + len(fragment) + 1 > 58:
            sentences.append(current + "。")
            current = fragment
        else:
            current += separator + fragment
    if current:
        sentences.append(current + "。")
    result = "".join(sentences)
    if len(result) <= max_chars:
        return result
    clipped = result[:max_chars]
    boundary = max(clipped.rfind("。"), clipped.rfind("；"))
    return clipped[:boundary + 1] if boundary >= max_chars // 2 else clipped.rstrip("，；") + "。"


def _has_explicit_rule(text: str) -> bool:
    return any(cue in text for cue in ("必须", "需要", "条件")) or (
        "如果" in text and any(cue in text for cue in ("则", "就"))
    )


def _blocks_for_offline_unit(up: UnitPlan, combined_text: str, evidence: list[VisualEvidence]) -> list[dict[str, Any]]:
    """按 detail_level 生成最小 content_blocks，视觉证据就近插入。"""
    blocks: list[dict[str, Any]] = []
    text = combined_text.strip() or up.title
    if up.detail_level == "mention":
        body = up.title
    elif up.detail_level == "brief":
        body = text[: max(60, min(up.target_chars, 180))]
    elif up.detail_level == "standard":
        body = text[: max(120, min(up.target_chars, 360))]
    else:
        body = text[: max(180, min(up.target_chars, 620))]
    blocks.append({
        "block_id": f"{up.plan_id}_b001",
        "type": "paragraph",
        "origin": "audio_backed",
        "text": body,
    })
    has_rule_cue = _has_explicit_rule(text)
    has_step_cue = any(cue in text for cue in ("第一", "第二", "步骤", "先", "再", "最后"))
    if up.detail_level in ("standard", "deep") and (
        ("rule" in up.knowledge_types and has_rule_cue)
        or ("procedure" in up.knowledge_types and has_step_cue)
    ):
        sentences = _split_sentences(text, 12)
        if "rule" in up.knowledge_types:
            items = [sentence for sentence in sentences if _has_explicit_rule(sentence)][:4]
        else:
            items = [
                sentence for sentence in sentences
                if any(cue in sentence for cue in ("第一", "第二", "步骤", "先", "再", "最后"))
            ][:4]
        if items:
            blocks.append({
                "block_id": f"{up.plan_id}_b002",
                "type": "rule_list" if "rule" in up.knowledge_types else "steps",
                "origin": "audio_backed",
                "items": items,
            })
    selected = [item for item in evidence if item.decision == "select" and item.matched_knowledge_point_id == up.plan_id]
    if selected:
        item = selected[0]
        offset = len(blocks) + 1
        blocks.append({
            "block_id": f"{up.plan_id}_b{offset:03d}",
            "type": "visual_group",
            "origin": "visual_backed",
            "binding_id": item.evidence_id,
            "lead_in": f"看图重点：{item.why_useful}",
            "caption": item.suggested_caption,
            "takeaway": item.explanation_for_reader,
            "source_timestamp": item.source_timestamp or item.timestamp,
            "scene_cluster_id": item.scene_cluster_id or item.dedup_group_id,
        })
    if up.detail_level == "deep" and up.expansion_allowed:
        blocks.append({
            "block_id": f"{up.plan_id}_b{len(blocks) + 1:03d}",
            "type": "understanding_tip",
            "origin": "model_aid",
            "text": "复习时优先核对来源中的判断条件、边界和例外，不要把课堂没有展开的结论继续外推。",
        })
    return blocks


def organize_offline(
    lesson_plan: LessonPlan,
    transcript: dict,
    content_level: str,
    visual_evidence: list[VisualEvidence] | None = None,
) -> list[KnowledgeUnit]:
    """离线构造知识单元：从写作计划和转写内容生成 KnowledgeUnit。"""
    from .course_ir import build_course_ir, course_ir_to_units
    from .dedup import run_dedup_gate

    course_ir = build_course_ir(lesson_plan, transcript, visual_evidence)
    units, _ = run_dedup_gate(course_ir_to_units(course_ir, content_level))
    return units


def _request_course_ir_organizing(
    lesson_plan: LessonPlan,
    transcript: dict,
    content_level: str,
    settings: dict,
    visual_evidence: list[VisualEvidence] | None,
    *,
    cloud_port: Any,
    cancel_check=None,
    brief: EditorialBrief | None = None,
) -> tuple[dict[str, Any], str, list[Any], dict[str, int], dict[str, list[str]]]:
    """Build, pre-batch and send only the compact CourseIR projection."""
    from .cloud_payload import (
        CloudPayloadError,
        build_cloud_payload,
        plan_payload_batches,
        validate_cloud_response,
    )
    from .course_ir import build_course_ir

    course_ir = build_course_ir(lesson_plan, transcript, visual_evidence)
    payload = build_cloud_payload(course_ir)
    from ..providers import cloud_output_limit
    from ..utils import cloud_optional_output_limit
    budget = settings.get("budget", {})
    max_chars = int(budget.get("max_input_chars", 60000))
    max_tokens = cloud_output_limit(settings)
    if content_level == "丰富":
        max_tokens = cloud_output_limit(settings, "rich_max_output_tokens", max_tokens)
    request_max_tokens = cloud_optional_output_limit(
        settings, "organizer_request_max_output_tokens", None,
    )
    # Reserve room for the stable instruction/schema wrapper before batching.
    payload_char_budget = max(1000, max_chars - 7000)
    max_units_per_batch = max(1, int(budget.get("organizer_max_units_per_batch", 1) or 1))
    batches = plan_payload_batches(
        payload,
        payload_char_budget,
        max_tokens,
        max_units_per_batch=max_units_per_batch,
    )

    decision_text = ""
    if lesson_plan.editorial_decision:
        decision = EditorialDecision.from_dict(lesson_plan.editorial_decision)
        decision_text = (
            f"结构模式：{decision.structure_mode}；"
            f"课程主线：{decision.core_thread}；"
            f"重点优先：{', '.join(decision.focus_priorities) if decision.focus_priorities else '由模型判断'}；"
            f"排序策略：{decision.sequence_policy}；"
            f"理由：{decision.decision_reason}"
        )

    brief_text = brief.text if brief else ""
    prompts = [
        compose_course_ir_prompt(
            payload_json=batch.payload.json_text(),
            content_level=content_level,
            max_tokens=max_tokens,
            editorial_brief=brief_text,
            editorial_decision=decision_text,
        )
        for batch in batches
    ]
    if any(len(prompt) > max_chars for prompt in prompts):
        raise CloudPayloadError("compact CourseIR 加固定指令后超过输入预算；未发送请求")

    audit_path_value = settings.get("_cloud_payload_audit_path")
    if audit_path_value:
        write_json(Path(str(audit_path_value)), {
            "version": payload.version,
            "payload": payload.to_dict(),
            "stats": {
                "chars": payload.char_count,
                "batch_count": len(batches),
                "source_count": len(payload.sources),
                "unit_count": len(payload.units),
                "claim_count": len(payload.claims),
                "visual_count": len(payload.visuals),
            },
            "batches": [{
                "batch_id": batch.batch_id,
                "chars": batch.char_count,
                "unit_ids": sorted(batch.allowed_ids.unit_ids),
            } for batch in batches],
        })

    source_blocks = {source.source_id: list(source.segment_ids) for source in course_ir.sources}
    sections: list[dict[str, Any]] = []
    objectives: list[str] = []
    checklist: list[str] = []
    open_questions: list[str] = []
    knowledge_threads: list[str] = []
    attempts: list[Any] = []
    usage_total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    models_used: list[str] = []
    document_title = ""
    overview_rows: list[str] = []
    for batch_index, (batch, prompt) in enumerate(zip(batches, prompts), 1):
        expected_plan_ids = set(batch.allowed_ids.unit_ids)
        batch_source_blocks = {
            source_id: source_blocks[source_id]
            for source_id in batch.allowed_ids.source_ids
        }
        source_ids_by_plan = {
            str(unit.get("id", "")): [
                str(source_id) for source_id in unit.get("source_ids", [])
                if str(source_id) in batch_source_blocks
            ]
            for unit in batch.payload.units
            if str(unit.get("id", ""))
        }
        expected_plan_order = [
            str(unit.get("id", "")) for unit in batch.payload.units if str(unit.get("id", ""))
        ]
        claim_ids_by_plan: dict[str, list[str]] = {}
        for claim in batch.payload.claims:
            plan_id = str(claim.get("unit_id", "") or "")
            claim_id = str(claim.get("id", "") or "")
            if plan_id and claim_id:
                claim_ids_by_plan.setdefault(plan_id, []).append(claim_id)

        def validate(value: dict[str, Any]) -> None:
            _validate_organizer_payload(
                value,
                expected_plan_ids,
                batch_source_blocks,
                source_ids_by_plan=source_ids_by_plan,
                claim_ids_by_plan=claim_ids_by_plan,
                expected_plan_order=expected_plan_order,
            )
            validate_cloud_response(value, batch.allowed_ids)

        request_payload = {
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "max_tokens": request_max_tokens or max_tokens,
            "omit_max_tokens": request_max_tokens is None,
        }
        parsed, request_info = cloud_port.request_json_with_info(
            request_payload,
            validator=validate,
            stage="organizing",
            cancel_check=cancel_check or (lambda: False),
        )
        request_info = dict(request_info)
        model = str(request_info.get("model", ""))
        batch_attempts = list(request_info.get("attempts", []))
        usage = dict(request_info.get("usage", {}))
        if model not in models_used:
            models_used.append(model)
        attempts.extend(batch_attempts)
        _sum_usage(usage_total, usage)
        document_title = document_title or str(parsed.get("document_title", "")).strip()
        overview = str(parsed.get("overview", "")).strip()
        if overview:
            overview_rows.append(overview)
        objectives.extend(_string_list(parsed.get("learning_objectives"), 20))
        sections.extend([dict(item) for item in parsed.get("sections", []) if isinstance(item, dict)])
        review = parsed.get("review", {}) if isinstance(parsed.get("review"), dict) else {}
        thread = str(review.get("knowledge_thread", "")).strip()
        if thread:
            knowledge_threads.append(thread)
        checklist.extend(_string_list(review.get("checklist"), 20))
        open_questions.extend(_string_list(review.get("open_questions"), 20))

    combined = {
        "document_title": document_title,
        "overview": "\n".join(dict.fromkeys(overview_rows)),
        "learning_objectives": list(dict.fromkeys(objectives)),
        "sections": sections,
        "review": {
            "knowledge_thread": "；".join(dict.fromkeys(knowledge_threads)),
            "checklist": list(dict.fromkeys(checklist)),
            "open_questions": list(dict.fromkeys(open_questions)),
        },
    }
    usage_total["source_chars"] = payload.char_count
    return combined, " + ".join(models_used), attempts, usage_total, source_blocks


def organize_cloud(
    lesson_plan: LessonPlan,
    transcript: dict,
    content_level: str,
    settings: dict,
    frame_semantics: list[FrameSemantic] | None = None,
    visual_evidence: list[VisualEvidence] | None = None,
    *,
    cloud_port: Any,
    cancel_check=None,
    brief: EditorialBrief | None = None,
) -> tuple[list[KnowledgeUnit], dict[str, Any]]:
    """云端专业化整理，返回 (units, cloud_info)。"""
    parsed, model, attempts, usage, source_blocks = _request_course_ir_organizing(
        lesson_plan, transcript, content_level, settings, visual_evidence,
        cloud_port=cloud_port, cancel_check=cancel_check, brief=brief,
    )

    # 构建 plan_id → UnitPlan 映射
    plan_map: dict[str, UnitPlan] = {}
    for ch in lesson_plan.chapters:
        for up in ch.unit_plans:
            plan_map[up.plan_id] = up

    # 解析知识单元
    units: list[KnowledgeUnit] = []
    unit_index = 0
    all_bindings: list[dict[str, Any]] = []
    for section in parsed.get("sections", []):
        # 收集章节级 visual_bindings
        for vb_data in section.get("visual_bindings", []):
            all_bindings.append(vb_data)

        for point in section.get("knowledge_points", []):
            unit_index += 1
            block_ids = point.get("source_block_ids", [])
            segment_ids: list[str] = []
            for bid in block_ids:
                segment_ids.extend(source_blocks.get(bid, []))

            plan_id = str(point.get("plan_id", "")).strip()
            up = plan_map.get(plan_id)
            if not segment_ids and up:
                segment_ids = [
                    sid for sid in up.source_segment_ids
                    if sid not in segment_ids
                ]
            ktype = "concept"
            if up and up.knowledge_types:
                ktype = up.knowledge_types[0]

            unit = KnowledgeUnit(
                unit_id=f"unit_{unit_index:04d}",
                type=ktype,
                title=str(point.get("statement", "")).strip(),
                importance=up.role if up else "core",
                definition_or_conclusion=str(point.get("explanation", "")).strip(),
                prerequisites=[],
                branches=[],
                procedure=_string_list(point.get("steps")),
                rules=[],
                exceptions=_string_list(point.get("conditions")),
                positive_examples=_string_list(point.get("examples")),
                negative_examples=[],
                pitfalls=_string_list(point.get("pitfalls")),
                unresolved=[],
                evidence_refs=[{"segment_ids": segment_ids}] if segment_ids else [],
                plan_id=plan_id,
                detail_level=up.detail_level if up else "",
                facet_status=dict(point.get("facet_status", {})),
                content_blocks=list(point.get("content_blocks", [])),
                visual_bindings=[
                    vb for vb in all_bindings
                    if vb.get("unit_id") == plan_id or vb.get("unit_id") == f"unit_{unit_index:04d}"
                ],
                visual_evidence=[
                    item.to_dict()
                    for item in (visual_evidence or [])
                    if item.matched_knowledge_point_id == plan_id and item.decision == "select"
                ],
            )
            # 从 details 补充
            details = _string_list(point.get("details"))
            if details:
                unit.branches = [{"detail": d} for d in details]
            if point.get("editorial_note"):
                unit.unresolved = [str(point["editorial_note"])]
            if point.get("review_tip"):
                unit.exceptions = [str(point["review_tip"])]

            units.append(unit)

    # 复习信息
    review = parsed.get("review", {})
    cloud_info = {
        "model": model,
        "attempts": [a.__dict__ if hasattr(a, "__dict__") else dict(a) for a in attempts],
        "usage": usage,
        "document_title": str(parsed.get("document_title", "")).strip(),
        "overview": str(parsed.get("overview", "")).strip(),
        "learning_objectives": _string_list(parsed.get("learning_objectives")),
        "review": review if isinstance(review, dict) else {},
        "raw_sections": parsed.get("sections", []),
        "visual_bindings": all_bindings,
    }
    return units, cloud_info


def _sum_usage(total: dict[str, int], usage: dict[str, Any]) -> None:
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        total[key] = int(total.get(key, 0)) + int(usage.get(key, 0) or 0)


def _string_list(value: object, limit: int = 12) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()][:limit]


def build_units(
    lesson_plan: LessonPlan,
    transcript: dict,
    content_level: str,
    settings: dict,
    cloud: bool = False,
    frame_semantics: list[FrameSemantic] | None = None,
    visual_evidence: list[VisualEvidence] | None = None,
    *,
    cloud_port: Any = None,
    cancel_check=None,
    event_sink=None,
    brief: EditorialBrief | None = None,
) -> tuple[list[KnowledgeUnit], dict[str, Any]]:
    """生成知识单元；缓存由 execution Step 统一管理。"""
    cloud_info: dict[str, Any] = {}
    if cloud and transcript.get("segments"):
        if cloud_port is None:
            raise ValueError("云端整理缺少 CloudJsonPort")
        try:
            units, cloud_info = organize_cloud(
                lesson_plan,
                transcript,
                content_level,
                settings,
                frame_semantics,
                visual_evidence,
                cloud_port=cloud_port,
                cancel_check=cancel_check,
                brief=brief,
            )
            if not _has_plan_coverage(lesson_plan, units):
                raise ValueError(f"云端整理覆盖不足：{len(units)}/{len(lesson_plan.all_unit_plans)}")
        except TaskCancelled:
            raise
        except Exception as exc:
            from .cloud_payload import CloudPayloadError
            if isinstance(exc, CloudPayloadError):
                raise
            raise CloudPayloadError(
                f"CourseIR 云端阶段未完成：{type(exc).__name__}: {exc}"
            ) from exc
    else:
        units = organize_offline(lesson_plan, transcript, content_level, visual_evidence)

    if cloud:
        from .dedup import run_dedup_gate
        units, dedup_report = run_dedup_gate(units)
        cloud_info["dedup_report"] = dedup_report.to_dict()

    return units, cloud_info
