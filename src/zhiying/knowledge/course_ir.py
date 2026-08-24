"""Build the canonical CourseIR and compose offline units from uniquely owned claims."""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Any

from .schema import Claim, ContentBlock, CourseIR, CourseUnit, KnowledgeUnit, LessonPlan, SourceBlock, VisualEvidence

COURSE_IR_VERSION = 1


def _normalize(text: str) -> str:
    return re.sub(r"[\s，。；;：:、！？!?（）()\[\]【】]+", "", str(text)).lower()


def _claim_fingerprint(text: str) -> str:
    return hashlib.sha256(_normalize(text).encode("utf-8")).hexdigest()[:16]


def build_source_blocks(transcript: dict[str, Any]) -> list[SourceBlock]:
    """Return the same stable block_0001 mapping used by cloud planning/writing."""
    from ..media.transcript import merge_transcript_segments

    merged = merge_transcript_segments(transcript.get("segments", []), max_chars=180, max_seconds=45.0)
    canonical_by_text: dict[str, str] = {}
    result: list[SourceBlock] = []
    for index, row in enumerate(merged, start=1):
        source_id = f"block_{index:04d}"
        normalized = _normalize(row.get("text", ""))
        canonical = canonical_by_text.setdefault(normalized, source_id) if normalized else source_id
        duplicate = canonical != source_id
        result.append(SourceBlock(
            source_id=source_id,
            start_seconds=float(row.get("start_seconds", 0.0)),
            end_seconds=float(row.get("end_seconds", 0.0)),
            text=str(row.get("text", "")),
            segment_ids=list(row.get("source_segment_ids", [])),
            repeat_group_id=f"repeat_{canonical}" if duplicate else "",
            canonical_source_id=canonical,
            adds_new_information=not duplicate,
        ))
    return result


def _sentences(text: str) -> list[str]:
    return [part.strip(" ，。；;！？!?\n") for part in re.split(r"[。；;！？!?\n]+", text) if part.strip(" ，。；;！？!?\n")]


def _claim_kind(text: str, ordinal: int) -> tuple[str, str]:
    if any(cue in text for cue in ("第一", "第二", "第三", "步骤", "先", "再", "最后")):
        return "step", "steps"
    if any(cue in text for cue in ("例如", "比如", "举例", "案例")):
        return "example", "example"
    if any(cue in text for cue in ("不要", "不能", "容易", "误区", "易错", "陷阱")):
        return "pitfall", "pitfall"
    if any(cue in text for cue in ("如果", "只有", "必须", "需要", "条件", "除非", "例外", "边界")):
        return "condition", "rule_list"
    return ("conclusion", "paragraph") if ordinal == 0 else ("explanation", "paragraph")


def build_course_ir(
    lesson_plan: LessonPlan,
    transcript: dict[str, Any],
    visual_evidence: list[VisualEvidence] | None = None,
) -> CourseIR:
    sources = build_source_blocks(transcript)
    source_by_id = {item.source_id: item for item in sources}
    source_ids_by_segment: dict[str, list[str]] = defaultdict(list)
    for source in sources:
        for segment_id in source.segment_ids:
            source_ids_by_segment[segment_id].append(source.source_id)

    units: list[CourseUnit] = []
    claims: list[Claim] = []
    for chapter in lesson_plan.chapters:
        for plan in chapter.unit_plans:
            source_ids = list(dict.fromkeys(
                source_id
                for segment_id in plan.source_segment_ids
                for source_id in source_ids_by_segment.get(segment_id, [])
            ))
            unit_type = plan.knowledge_types[0] if plan.knowledge_types else "concept"
            units.append(CourseUnit(
                unit_id=plan.plan_id,
                chapter_id=chapter.chapter_id,
                title=plan.title,
                type=unit_type,
                importance=plan.role,
                depth=plan.detail_level,
                source_ids=source_ids,
                source_segment_ids=list(plan.source_segment_ids),
                required_facets=list(plan.required_facets),
                expansion_allowed=plan.expansion_allowed,
            ))
            seen: dict[str, Claim] = {}
            sentence_rows: list[tuple[str, str]] = []
            for source_id in source_ids:
                source = source_by_id[source_id]
                if not source.adds_new_information and source.canonical_source_id in source_by_id:
                    continue
                sentence_rows.extend((sentence, source_id) for sentence in _sentences(source.text))
            if plan.detail_level == "mention" and not sentence_rows and plan.title:
                sentence_rows = [(plan.title, source_ids[0] if source_ids else "")]
            for ordinal, (sentence, source_id) in enumerate(sentence_rows):
                fingerprint = _claim_fingerprint(sentence)
                if not fingerprint:
                    continue
                if fingerprint in seen:
                    if source_id and source_id not in seen[fingerprint].source_ids:
                        seen[fingerprint].source_ids.append(source_id)
                    continue
                kind, display_block = _claim_kind(sentence, ordinal)
                claim = Claim(
                    claim_id=f"claim_{plan.plan_id}_{len(seen) + 1:03d}_{fingerprint[:6]}",
                    unit_id=plan.plan_id,
                    kind=kind,
                    text=sentence,
                    source_ids=[source_id] if source_id else [],
                    origin="audio_backed",
                    fingerprint=fingerprint,
                    display_block=display_block,
                )
                seen[fingerprint] = claim
            claims.extend(seen.values())

    selected_visuals = [
        item.to_dict() for item in (visual_evidence or []) if item.decision == "select"
    ]
    duration = max((source.end_seconds for source in sources), default=0.0)
    return CourseIR(
        schema_version=COURSE_IR_VERSION,
        course={
            "id": str(transcript.get("video_id", "course")),
            "domain": lesson_plan.domain,
            "form": lesson_plan.course_form,
            "duration": duration,
        },
        sources=sources,
        units=units,
        claims=claims,
        visuals=selected_visuals,
    )


def route_claims_to_blocks(claims: list[Claim]) -> list[ContentBlock]:
    """Route each claim to exactly one primary display block."""
    if not claims:
        return []
    grouped: dict[str, list[Claim]] = defaultdict(list)
    seen: set[str] = set()
    for claim in claims:
        fingerprint = claim.fingerprint or _claim_fingerprint(claim.text)
        if not claim.text.strip() or fingerprint in seen:
            continue
        seen.add(fingerprint)
        grouped[claim.display_block].append(claim)
    order = ("paragraph", "rule_list", "steps", "example", "pitfall", "understanding_tip")
    blocks: list[ContentBlock] = []
    unit_id = claims[0].unit_id
    for block_type in order:
        rows = grouped.get(block_type, [])
        if not rows:
            continue
        source_ids = list(dict.fromkeys(source_id for row in rows for source_id in row.source_ids))
        common = {
            "block_id": f"{unit_id}_b{len(blocks) + 1:03d}",
            "type": block_type,
            "origin": rows[0].origin,
            "claim_ids": [row.claim_id for row in rows],
            "source_ids": source_ids,
        }
        if block_type in {"rule_list", "steps", "example", "pitfall"}:
            blocks.append(ContentBlock(**common, items=[row.text for row in rows]))
        else:
            blocks.append(ContentBlock(**common, text="。".join(row.text.rstrip("。") for row in rows) + "。"))
    return blocks


def course_ir_to_units(course_ir: CourseIR, content_level: str) -> list[KnowledgeUnit]:
    del content_level  # depth is already resolved by planning
    claims_by_unit: dict[str, list[Claim]] = defaultdict(list)
    for claim in course_ir.claims:
        claims_by_unit[claim.unit_id].append(claim)
    visuals_by_unit: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for visual in course_ir.visuals:
        unit_id = str(visual.get("matched_knowledge_point_id", visual.get("unit_id", "")))
        if unit_id:
            visuals_by_unit[unit_id].append(visual)

    result: list[KnowledgeUnit] = []
    for index, course_unit in enumerate(course_ir.units, start=1):
        blocks = [block.to_dict() for block in route_claims_to_blocks(claims_by_unit[course_unit.unit_id])]
        for visual in visuals_by_unit.get(course_unit.unit_id, [])[:1]:
            blocks.append({
                "block_id": f"{course_unit.unit_id}_b{len(blocks) + 1:03d}",
                "type": "visual_group",
                "origin": "visual_backed",
                "binding_id": str(visual.get("evidence_id", "")),
                "lead_in": f"看图重点：{visual.get('why_useful', '')}".rstrip("："),
                "caption": str(visual.get("suggested_caption", "")),
                "takeaway": str(visual.get("explanation_for_reader", "")),
                "source_timestamp": visual.get("source_timestamp", visual.get("timestamp", 0.0)),
                "scene_cluster_id": str(visual.get("scene_cluster_id", visual.get("dedup_group_id", ""))),
            })
        if course_unit.depth == "deep" and course_unit.expansion_allowed:
            blocks.append({
                "block_id": f"{course_unit.unit_id}_b{len(blocks) + 1:03d}",
                "type": "understanding_tip",
                "origin": "model_aid",
                "text": "复习时优先核对来源中的判断条件、边界和例外，不要把课堂没有展开的结论继续外推。",
                "claim_ids": [],
                "source_ids": [],
            })
        paragraph = next((block.get("text", "") for block in blocks if block.get("type") == "paragraph"), "")
        first_item = next((block.get("items", [""])[0] for block in blocks if block.get("items")), "")
        result.append(KnowledgeUnit(
            unit_id=f"unit_{index:04d}",
            type=course_unit.type,
            title=course_unit.title,
            importance=course_unit.importance,
            definition_or_conclusion=paragraph or first_item or course_unit.title,
            evidence_refs=[{"segment_ids": list(course_unit.source_segment_ids)}] if course_unit.source_segment_ids else [],
            plan_id=course_unit.unit_id,
            detail_level=course_unit.depth,
            facet_status={facet: "present" for facet in course_unit.required_facets},
            content_blocks=blocks,
            visual_evidence=list(visuals_by_unit.get(course_unit.unit_id, [])),
        ))
    return result
