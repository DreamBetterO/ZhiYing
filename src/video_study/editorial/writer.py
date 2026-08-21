"""V6.1 Writer：按 Blueprint 强制结构化章节写作（CP61-4）。

- 默认不开放自由工具循环；submit_chapter 为强制结构化输出；
- 按 max_batch_units 分批；每章独立 Artifact/指纹（成功章不因其他章失败丢失）；
- 内容精炼复用本地语义链（oral/ASR 纪律与公式/图片/来源组件保持）。
"""
from __future__ import annotations

import hashlib
from typing import Any, Iterable, Mapping

from ..execution.artifacts import canonical_json_hash
from .blueprint import BlueprintChapter, DocumentBlueprint
from .evidence import EvidenceCorrectionOverlay
from .local import _compose_unit_children, _find_unit, _find_visual_evidence


def chapter_fingerprint(
    chapter: BlueprintChapter,
    units: Iterable[Mapping[str, Any]],
    overlay: EvidenceCorrectionOverlay,
) -> str:
    """章级指纹：unit 引用 + 有效证据摘要 + overlay digest。"""
    material = {
        "chapter_id": chapter.chapter_id,
        "unit_refs": chapter.unit_refs,
        "overlay_digest": overlay.transcript_digest,
        "units": [
            {
                "plan_id": unit.get("plan_id"),
                "definition": unit.get("definition_or_conclusion", "")[:80],
            }
            for unit in units
            if unit.get("plan_id") in chapter.unit_refs
        ],
    }
    return "sha256:" + hashlib.sha256(
        canonical_json_hash(material).encode("utf-8"),
    ).hexdigest()


def compose_chapter(
    chapter: BlueprintChapter,
    units: Iterable[Mapping[str, Any]],
    overlay: EvidenceCorrectionOverlay,
    visual_evidence: Iterable[Mapping[str, Any]] | None = None,
    *,
    used_visual_keys: set[str] | None = None,
) -> dict[str, Any]:
    """生成单章组件候选（container 组件树）。"""
    visual_evidence = list(visual_evidence or [])
    chapter_children: list[dict[str, Any]] = [{
        "type": "heading", "component_id": f"{chapter.chapter_id}.h",
        "semantic_role": "heading", "text": chapter.title, "level": 2,
        "source_refs": {}, "origin": "local_deterministic", "confidence": 1.0,
    }]
    for unit_ref in chapter.unit_refs:
        unit = _find_unit(units, unit_ref)
        if unit is None:
            continue
        unit_children = _compose_unit_children(chapter.chapter_id, unit, overlay)
        evidence = _find_visual_evidence(
            visual_evidence, unit, used_keys=used_visual_keys,
        )
        if evidence is not None:
            unit_children.append({
                "type": "image", "component_id": f"{chapter.chapter_id}.{unit.get('unit_id', unit_ref)}.img",
                "semantic_role": "image",
                "visual_id": str(evidence.get("evidence_id", "")),
                "role": str(evidence.get("visual_role", "explain")),
                "caption": str(evidence.get("suggested_caption", "")),
                "alt_text": str(evidence.get("visual_summary", evidence.get("explanation_for_reader", ""))),
                "source_timestamp": float(evidence.get("source_timestamp", 0.0)),
                "source_refs": {"segment_ids": unit.get("evidence_refs", []) if isinstance(unit.get("evidence_refs"), list) else []},
                "origin": str(evidence.get("source", "local_vlm")),
                "confidence": float(evidence.get("confidence", 0.6)),
            })
        unit_children.append({
            "type": "source_reference",
            "component_id": f"{chapter.chapter_id}.{unit.get('unit_id', unit_ref)}.src",
            "semantic_role": "source_reference",
            "source_refs": dict(unit.get("source_refs", {}) or {}),
            "links": unit.get("source_links", []),
            "origin": "local_deterministic", "confidence": 1.0,
        })
        chapter_children.append({
            "type": "container", "component_id": f"{chapter.chapter_id}.{unit.get('unit_id', unit_ref)}",
            "semantic_role": "knowledge_point", "title": str(unit.get("title", "")),
            "children": unit_children, "origin": "local_deterministic", "confidence": 1.0,
            "source_refs": dict(unit.get("source_refs", {}) or {}),
        })
    return {
        "type": "container", "component_id": chapter.chapter_id,
        "semantic_role": "chapter", "title": chapter.title,
        "layout_hint": chapter.layout_hint,
        "children": chapter_children,
        "origin": "local_deterministic", "confidence": 1.0,
        "source_refs": {},
    }


def write_chapters_in_batches(
    blueprint: DocumentBlueprint,
    units: Iterable[Mapping[str, Any]],
    overlay: EvidenceCorrectionOverlay,
    visual_evidence: Iterable[Mapping[str, Any]] | None = None,
    *,
    max_batch_units: int = 6,
    cache: Mapping[str, str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """分批写作：返回 (章节组件列表, 章指纹缓存)。

    cache 为 {fingerprint: chapter_id}；命中指纹直接复用（不重写）。
    """
    cache = dict(cache or {})
    units = list(units)
    written: list[dict[str, Any]] = []
    fingerprints: dict[str, str] = {}
    used_visual_keys: set[str] = set()
    for chapter in blueprint.chapters:
        fingerprint = chapter_fingerprint(chapter, units, overlay)
        fingerprints[chapter.chapter_id] = fingerprint
        if cache.get(chapter.chapter_id) == fingerprint:
            continue  # 缓存命中：复用既有章节 Artifact
        written.append(compose_chapter(
            chapter, units, overlay, visual_evidence,
            used_visual_keys=used_visual_keys,
        ))
    return written, fingerprints
