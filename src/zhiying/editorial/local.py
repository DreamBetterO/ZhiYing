"""V6.1 本地确定性编辑链（CP61-1）：LocalBlueprintPolicy / LocalDocumentComposer / LocalDeterministicRepair。

本地链不是旧模板 fallback，而是完整语义编译链：
- LocalBlueprintPolicy：把 LessonPlan + EditorialPolicy 编译为 DocumentBlueprint v2；
- LocalDocumentComposer：按 Blueprint 消费 KnowledgeUnits + EvidenceCorrectionOverlay + 视觉证据，
  产出合法 Document v3.1 组件树；
- LocalDeterministicRepair：最小确定性修复（空标题等）。

质量纪律：
- 不把大段原始口语/ASR 错词伪装成“规则/步骤”；无法可靠精炼时生成简洁、可追踪的降级资料；
- 公式进入 equation 组件（math AST 本地未解析时记录降级）；
- 图片只在 select 证据且满足职责时出现；no_match 不兜底；
- 原始 transcript 不可变（只读 + overlay）。
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable, Mapping

from ..knowledge.schema import LessonPlan, UnitPlan
from .blueprint import BlueprintChapter, DocumentBlueprint
from .document import build_v31_document, make_component, walk_components
from .evidence import EvidenceCorrection, EvidenceCorrectionOverlay, _TERMINOLOGY_RULES
from .policy import EditorialPolicy

# 口语填充词（本地精炼时移除，避免直接复制进正文）
_ORAL_FILLERS = (
    "那好了", "那好吧", "好 下面", "好 那", "那这样", "那 那", "好吧",
    "就是说", "也就是说", "同学们", "有没有", "我们说", "我们来说", "那好",
)

_WHITESPACE_PATTERN = re.compile(r"\s+")
_SENTENCE_PATTERN = re.compile(r"[^。！？；;]+[。！？；;]?")

_FORMULA_MARKERS = ("∫", "∮", "∑", "∏", "√", "dx", "dy", "=")

_MODE_STRATEGIES: dict[str, tuple[str, ...]] = {
    "concept": ("definition", "properties", "boundary_conditions"),
    "procedure": ("goal", "steps", "result"),
    "case": ("problem", "approach", "derivation", "conclusion"),
    "comparison": ("objects", "dimensions", "differences"),
    "mixed": ("overview", "knowledge_points"),
}


def _strip_fillers(text: str) -> str:
    stripped = text
    for filler in _ORAL_FILLERS:
        stripped = stripped.replace(filler, "")
    stripped = _WHITESPACE_PATTERN.sub(" ", stripped)
    stripped = re.sub(r"\s+([，。；、！？])", r"\1", stripped)
    return stripped.strip()


def _contains_known_asr_error(text: str) -> bool:
    for raw, _candidate, _confidence, context in _TERMINOLOGY_RULES:
        if raw in text and (context is None or re.search(context, text)):
            return True
    return False


def _split_sentences(text: str) -> list[str]:
    return [sentence.strip() for sentence in _SENTENCE_PATTERN.findall(text) if sentence.strip()]


def _chapter_mode(plan: LessonPlan, chapter_id: str) -> str:
    for chapter in plan.chapters:
        if chapter.chapter_id == chapter_id:
            types = [ktype for unit in chapter.unit_plans for ktype in unit.knowledge_types]
            if "case" in types:
                return "case"
            if "comparison" in types:
                return "comparison"
            if "procedure" in types:
                return "procedure"
            if "concept" in types:
                return "concept"
            return "mixed"
    return "mixed"


def _front_matter_sections(policy: EditorialPolicy) -> list[str]:
    forbidden = {constraint.code for constraint in policy.forbidden}
    required = {constraint.code for constraint in policy.required}
    sections: list[str] = []
    for code in ("overview", "learning_objectives"):
        if code in forbidden:
            continue
        if code in required:
            sections.append(code)
    return sections


def build_local_blueprint(plan: LessonPlan, policy: EditorialPolicy) -> DocumentBlueprint:
    """本地策略：把 LessonPlan + EditorialPolicy 编译为 DocumentBlueprint v2。"""
    chapters: list[BlueprintChapter] = []
    source_refs: list[str] = []
    visual_roles: list[str] = []
    unit_ids: list[str] = []
    for chapter in plan.chapters:
        mode = _chapter_mode(plan, chapter.chapter_id)
        unit_ids.extend(unit.plan_id for unit in chapter.unit_plans)
        for unit in chapter.unit_plans:
            source_refs.extend(unit.source_segment_ids)
            if unit.needs_visual and unit.visual_need.role not in visual_roles:
                visual_roles.append(unit.visual_need.role)
        target_chars = sum(max(80, unit.target_chars) for unit in chapter.unit_plans) or 260
        depth = max((unit.detail_level for unit in chapter.unit_plans), default="standard")
        chapters.append(BlueprintChapter(
            chapter_id=chapter.chapter_id,
            title=chapter.title,
            mode=mode,
            unit_refs=[unit.plan_id for unit in chapter.unit_plans],
            component_intents=list(_MODE_STRATEGIES.get(mode, _MODE_STRATEGIES["mixed"])),
            layout_hint="full_width",
            depth=depth,
            target_chars=target_chars,
        ))
    if not chapters:
        # 空规划：产出最小降级章节（课程内容不足以形成知识单元时仍可追踪）
        chapters.append(BlueprintChapter(
            chapter_id="chapter_001", title="课程内容", mode="mixed",
            unit_refs=[], component_intents=["overview"],
            layout_hint="full_width", depth="brief", target_chars=200,
        ))

    blueprint_id_digest = hashlib.sha256("|".join(unit_ids).encode("utf-8")).hexdigest()[:12]
    forbidden_codes = {constraint.code for constraint in policy.forbidden}
    required_codes = {constraint.code for constraint in policy.required}
    constraint_mapping = {
        code: ("forbidden" if code in forbidden_codes else "required" if code in required_codes else "preferred")
        for code in sorted(forbidden_codes | required_codes)
    }
    front_matter = _front_matter_sections(policy)
    blueprint = DocumentBlueprint(
        blueprint_id=f"bp_local_{blueprint_id_digest}",
        policy_version=policy.version,
        evidence_version=1,
        capability_version="renderer-capability-v1",
        document_type="course_notes",
        audience="学习者",
        purpose=policy.content_focus or "系统学习与复习",
        density=policy.density or "recommended",
        front_matter_policy={"sections": front_matter},
        navigation_policy={"fixed_numbering": False},
        chapters=chapters,
        component_intents=[
            intent for chapter in chapters for intent in chapter.component_intents
        ],
        source_refs=list(dict.fromkeys(source_refs)),
        visual_roles=visual_roles,
        formula_evidence_policy={"required": policy.formula_preference == "formula_priority"},
        layout_hints=["full_width"],
        constraint_mapping=constraint_mapping,
        component_fallbacks={
            "equation": "source_text_with_marker",
            "image": "skip_with_reason",
            "table": "paragraph",
        },
        unresolved_questions=[],
    )
    return blueprint


# ---------------------------------------------------------------------------
# LocalDocumentComposer
# ---------------------------------------------------------------------------

def _find_unit(units: Iterable[Mapping[str, Any]], plan_id: str) -> dict[str, Any] | None:
    for unit in units:
        if str(unit.get("plan_id", "")) == plan_id:
            return dict(unit)
    return None


def _visual_dedup_key(evidence: Mapping[str, Any]) -> str:
    return str(
        evidence.get("scene_cluster_id") or evidence.get("evidence_id")
        or evidence.get("frame_id") or evidence.get("image_path")
        or f"timestamp:{float(evidence.get('source_timestamp', 0) or 0):.3f}"
    )


def _find_visual_evidence(
    visual_evidence: Iterable[Mapping[str, Any]],
    unit: Mapping[str, Any],
    *,
    used_keys: set[str] | None = None,
    allow_unscoped: bool = True,
) -> dict[str, Any] | None:
    unit_ids = {item for item in {
        str(unit.get("plan_id", "")),
        str(unit.get("unit_id", "")),
        str(unit.get("matched_knowledge_point_id", "")),
    } if item}
    used_keys = used_keys if used_keys is not None else set()
    for evidence in visual_evidence:
        if str(evidence.get("decision", "")) != "select":
            continue
        evidence_ids = {item for item in {
            str(evidence.get("primary_unit_id", "")),
            str(evidence.get("matched_knowledge_point_id", "")),
            str(evidence.get("matched_knowledge_id", "")),
        } if item}
        key = _visual_dedup_key(evidence)
        if key in used_keys:
            continue
        if evidence_ids & unit_ids or (allow_unscoped and not evidence_ids):
            used_keys.add(key)
            return dict(evidence)
    return None


def _compose_unit_children(
    chapter_id: str,
    unit: Mapping[str, Any],
    overlay: EvidenceCorrectionOverlay,
) -> list[dict[str, Any]]:
    children: list[dict[str, Any]] = []
    unit_id = str(unit.get("unit_id", "") or unit.get("plan_id", ""))
    source_refs = dict(unit.get("source_refs", {}) or {})
    source_refs.setdefault("segment_ids", unit.get("evidence_refs", []) if isinstance(unit.get("evidence_refs"), list) else [])

    children.append(make_component(
        "heading", component_id=f"{chapter_id}.{unit_id}.h", semantic_role="heading",
        text=str(unit.get("title", "")), source_refs=source_refs,
    ))

    content_blocks = [
        dict(block) for block in unit.get("content_blocks", [])
        if isinstance(block, Mapping)
    ]
    if content_blocks:
        for index, block in enumerate(content_blocks, start=1):
            block_id = str(block.get("block_id") or f"block_{index:03d}")
            component_base = f"{chapter_id}.{unit_id}.{block_id}"
            block_type = str(block.get("type") or "paragraph")
            text = _strip_fillers(overlay.apply_to(str(block.get("text", ""))))
            items = [
                _strip_fillers(overlay.apply_to(str(item)))
                for item in block.get("items", []) if str(item).strip()
            ]
            if text:
                normalized_text = re.sub(r"\s+", "", text).strip("，。；;：:、")
                items = [
                    item for item in items
                    if re.sub(r"\s+", "", item).strip("，。；;：:、") != normalized_text
                ]
            if text and _contains_known_asr_error(text):
                children.append(make_component(
                    "callout", component_id=f"{component_base}.unresolved",
                    semantic_role="unresolved", title="待核对来源",
                    text="该内容块存在转写疑点，请对照原视频核对。",
                    source_refs=source_refs, confidence=0.3,
                ))
                continue
            if text:
                if block_type in {"example", "pitfall"}:
                    children.append(make_component(
                        "callout", component_id=f"{component_base}.body",
                        semantic_role=block_type,
                        title="例题" if block_type == "example" else "易错点",
                        text=text, source_refs=source_refs,
                    ))
                else:
                    children.append(make_component(
                        "paragraph", component_id=f"{component_base}.body",
                        semantic_role=block_type, text=text, source_refs=source_refs,
                    ))
            clean_items = [item for item in items if item and not _contains_known_asr_error(item)]
            if clean_items:
                children.append(make_component(
                    "list", component_id=f"{component_base}.items",
                    semantic_role=block_type, items=clean_items, source_refs=source_refs,
                ))
        return children

    raw = str(unit.get("definition_or_conclusion", "")).strip()
    effective = overlay.apply_to(raw)
    if _contains_known_asr_error(effective):
        # 无法可靠校正：不把大段原始口语伪装成规则/步骤，产出可追踪的降级说明
        children.append(make_component(
            "callout", component_id=f"{chapter_id}.{unit_id}.unresolved", semantic_role="unresolved",
            text="该知识点原始转写存在识别疑点，本地未能可靠校正，保留为待核对来源，请对照原视频核对。",
            source_refs=source_refs, confidence=0.3,
        ))
        return children

    refined = _strip_fillers(effective)
    for index, sentence in enumerate(_split_sentences(refined), start=1):
        if any(marker in sentence for marker in _FORMULA_MARKERS):
            children.append(make_component(
                "equation", component_id=f"{chapter_id}.{unit_id}.eq_{index:02d}",
                semantic_role="equation", latex=sentence, math_ast="",
                source_refs=source_refs, confidence=0.5,
            ))
        else:
            children.append(make_component(
                "paragraph", component_id=f"{chapter_id}.{unit_id}.p_{index:02d}",
                semantic_role="paragraph", text=sentence, source_refs=source_refs,
            ))

    # 列表字段（rules/procedure/pitfalls）：仅当不含 ASR 错词且非口语填充时才输出
    list_items: list[str] = []
    for field in ("rules", "procedure", "pitfalls"):
        values = unit.get(field, [])
        if not isinstance(values, list):
            continue
        for item in values:
            text = _strip_fillers(overlay.apply_to(str(item)))
            if not text or _contains_known_asr_error(text):
                continue
            if len(text) < 8 and not any(kw in text for kw in ("是", "条件", "步骤", "注意", "不能")):
                continue
            list_items.append(text)
    if list_items:
        children.append(make_component(
            "list", component_id=f"{chapter_id}.{unit_id}.list", semantic_role="key_points",
            items=list_items, source_refs=source_refs,
        ))
    return children


def compose_local_document(
    *,
    blueprint: DocumentBlueprint,
    units: Iterable[Mapping[str, Any]],
    overlay: EvidenceCorrectionOverlay,
    plan: LessonPlan | None = None,
    visual_evidence: Iterable[Mapping[str, Any]] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """按 Blueprint 编译 Document v3.1 组件树（纯本地，无云端/VLM）。"""
    visual_evidence = list(visual_evidence or [])
    components: list[dict[str, Any]] = []
    chapter_writing: dict[str, str] = {}
    used_visual_keys: set[str] = set()

    for chapter in blueprint.chapters:
        chapter_children: list[dict[str, Any]] = [
            make_component(
                "heading", component_id=f"{chapter.chapter_id}.h", semantic_role="heading",
                text=chapter.title,
            ),
        ]
        for unit_ref in chapter.unit_refs:
            unit = _find_unit(units, unit_ref)
            if unit is None:
                continue
            unit_children = _compose_unit_children(chapter.chapter_id, unit, overlay)
            evidence = _find_visual_evidence(
                visual_evidence, unit, used_keys=used_visual_keys,
            )
            if evidence is not None:
                unit_children.append(make_component(
                    "image", component_id=f"{chapter.chapter_id}.{unit.get('unit_id', unit_ref)}.img",
                    semantic_role="image",
                    visual_id=str(evidence.get("evidence_id", "")),
                    role=str(evidence.get("visual_role", "explain")),
                    caption=str(evidence.get("suggested_caption", "")),
                    alt_text=str(evidence.get("visual_summary", evidence.get("explanation_for_reader", ""))),
                    source_timestamp=float(evidence.get("source_timestamp", 0.0)),
                    source_refs={"segment_ids": unit.get("evidence_refs", []) if isinstance(unit.get("evidence_refs"), list) else []},
                    origin=str(evidence.get("source", "local_vlm")),
                    confidence=float(evidence.get("confidence", 0.6)),
                ))
            unit_children.append(make_component(
                "source_reference", component_id=f"{chapter.chapter_id}.{unit.get('unit_id', unit_ref)}.src",
                semantic_role="source_reference",
                source_refs=dict(unit.get("source_refs", {}) or {}),
                links=unit.get("source_links", []),
            ))
            chapter_children.append(make_component(
                "container", component_id=f"{chapter.chapter_id}.{unit.get('unit_id', unit_ref)}",
                semantic_role="knowledge_point",
                title=str(unit.get("title", "")),
                children=unit_children,
            ))
        components.append(make_component(
            "container", component_id=chapter.chapter_id, semantic_role="chapter",
            title=chapter.title, layout_hint=chapter.layout_hint,
            children=chapter_children,
        ))
        chapter_writing[chapter.chapter_id] = "local_deterministic"

    provenance = {
        "intent": "local_deterministic",
        "evidence_reconcile": "local_rules" if overlay.corrections else "skipped",
        "blueprint": "local_deterministic",
        "chapter_writing": chapter_writing,
        "page_audit": "local_rules",
        "final_repair": "local_deterministic",
        "target_chars": sum(chapter.target_chars for chapter in blueprint.chapters),
        "available_content_chars": sum(
            len(str(block.get("text", "")))
            + sum(len(str(item)) for item in block.get("items", []) if item is not None)
            for unit in units if isinstance(unit, Mapping)
            for block in unit.get("content_blocks", []) if isinstance(block, Mapping)
        ),
    }
    return build_v31_document(
        metadata=dict(metadata or {}),
        components=components,
        provenance=provenance,
    )


# ---------------------------------------------------------------------------
# LocalDeterministicRepair
# ---------------------------------------------------------------------------

def local_deterministic_repair(components: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """最小确定性修复：空标题/空 heading 回填稳定标题。"""
    repaired = [dict(component) for component in components]
    for index, component in enumerate(repaired, start=1):
        title = str(component.get("title", "")).strip()
        if component.get("semantic_role") == "chapter" and not title:
            component["title"] = f"章节 {index}"
            for child in component.get("children", []):
                if isinstance(child, dict) and child.get("type") == "heading" and not str(child.get("text", "")).strip():
                    child["text"] = component["title"]
    return repaired
