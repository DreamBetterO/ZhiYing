"""V6.1 Document v3.1 组件树模型与 validator（CP61-1 建立，CP61-2 接入原生渲染）。

Document v3.1 保持 schema_version=3、contract_version=document-v3.1：
- component tree 是唯一新写权威；
- 每个非装饰组件包含稳定 component_id、semantic_role、source_refs、origin、confidence；
- equation 保存规范 LaTeX（math AST 本地无法解析时留空并记录降级）；
- image 保存 visual_id、role、caption、alt_text、source_timestamp 与来源；
- 不保存 renderer 私有 XML。
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

DOCUMENT_CONTRACT_VERSION = "document-v3.1"
DOCUMENT_SCHEMA_VERSION = 3
RENDERER_CAPABILITY_VERSION = "renderer-capability-v1"

COMPONENT_TYPES = frozenset({
    "heading", "paragraph", "list", "equation", "table", "image",
    "container", "callout", "page_break", "source_reference",
})

# 装饰组件可豁免稳定字段要求
_DECORATIVE_TYPES = frozenset({"page_break"})

REQUIRED_NON_DECORATIVE_FIELDS = ("component_id", "semantic_role", "source_refs", "origin", "confidence")


def walk_components(components: Iterable[Mapping[str, Any]]) -> Iterable[dict[str, Any]]:
    for component in components:
        yield dict(component)
        yield from walk_components(component.get("children", []))


def make_component(
    component_type: str,
    *,
    component_id: str,
    semantic_role: str,
    source_refs: Mapping[str, Any] | None = None,
    origin: str = "local_deterministic",
    confidence: float = 0.5,
    **fields: Any,
) -> dict[str, Any]:
    """构造一个合法 Document v3.1 组件。"""
    if component_type not in COMPONENT_TYPES:
        raise ValueError(f"未知 Document v3.1 组件：{component_type}")
    component: dict[str, Any] = {
        "type": component_type,
        "component_id": component_id,
        "semantic_role": semantic_role,
        "source_refs": dict(source_refs or {}),
        "origin": origin,
        "confidence": max(0.0, min(1.0, float(confidence))),
        **fields,
    }
    return component


def build_v31_document(
    *,
    metadata: Mapping[str, Any],
    components: Iterable[Mapping[str, Any]],
    provenance: Mapping[str, Any] | None = None,
    overview: str = "",
    learning_objectives: list[str] | None = None,
) -> dict[str, Any]:
    """组装 Document v3.1（组件树为权威）。"""
    document = {
        "schema_version": DOCUMENT_SCHEMA_VERSION,
        "contract_version": DOCUMENT_CONTRACT_VERSION,
        "metadata": dict(metadata),
        "components": [dict(component) for component in components],
        "renderer_capability_manifest": {
            "components": sorted(COMPONENT_TYPES),
            "layout_hints": ["full_width", "image_text", "two_column", "compact_table"],
            "unknown_component_fallback": "paragraph",
            "capability_version": RENDERER_CAPABILITY_VERSION,
        },
        "provenance": dict(provenance or {}),
    }
    if overview:
        document["overview"] = overview
    if learning_objectives:
        document["learning_objectives"] = list(learning_objectives)
    return document


def validate_document_v31(document: Mapping[str, Any]) -> None:
    """校验 Document v3.1 合同；不通过抛 ValueError。"""
    if int(document.get("schema_version", 0)) != DOCUMENT_SCHEMA_VERSION:
        raise ValueError("Document 必须为 schema v3")
    if str(document.get("contract_version", "")) != DOCUMENT_CONTRACT_VERSION:
        raise ValueError("Document contract_version 必须为 document-v3.1")
    components = document.get("components")
    if not isinstance(components, list) or not components:
        raise ValueError("Document v3.1 缺少 components")
    for component in walk_components(components):
        component_type = str(component.get("type", ""))
        if component_type not in COMPONENT_TYPES:
            raise ValueError(f"未知 Document v3.1 组件：{component_type}")
        if component_type in _DECORATIVE_TYPES:
            continue
        missing = [field for field in REQUIRED_NON_DECORATIVE_FIELDS if component.get(field) in (None, "")]
        if missing:
            raise ValueError(
                f"组件 {component.get('component_id') or component_type} 缺少必需字段：{', '.join(missing)}",
            )
        if component_type == "equation":
            if not str(component.get("latex", "")).strip():
                raise ValueError("equation 组件缺少 latex")
        if component_type == "image":
            for field in ("visual_id", "role", "caption", "alt_text", "source_timestamp"):
                if field not in component:
                    raise ValueError(f"image 组件缺少 {field}")


def v31_to_legacy_sections(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    """只读投影：Document v3.1 组件树 → v2 sections 形状。

    仅用于聚合/历史只读路径（AggregateGraph 输入与诊断展示），
    不参与 v3.1 生产渲染；知识点 summary/statement/explanation 由组件文本确定性拼装。
    """
    sections: list[dict[str, Any]] = []
    for chapter in document.get("components", []):
        if chapter.get("type") != "container" or chapter.get("semantic_role") != "chapter":
            continue
        points: list[dict[str, Any]] = []
        summary_parts: list[str] = []
        for child in chapter.get("children", []):
            if child.get("type") == "heading":
                continue
            if child.get("type") == "container" and child.get("semantic_role") == "knowledge_point":
                statement = str(child.get("title", "") or "")
                explanation_parts: list[str] = []
                figures: list[dict[str, Any]] = []
                segment_ids: list[str] = []
                links: list[dict[str, Any]] = []
                start_seconds = 0.0
                end_seconds = 0.0
                source_label = ""
                source_url = ""
                for leaf in child.get("children", []):
                    leaf_type = leaf.get("type")
                    if leaf_type == "heading":
                        statement = statement or str(leaf.get("text", ""))
                    elif leaf_type in ("paragraph", "callout") and leaf.get("text"):
                        explanation_parts.append(str(leaf["text"]))
                    elif leaf_type == "list":
                        explanation_parts.extend(str(item) for item in leaf.get("items", []) if str(item).strip())
                    elif leaf_type == "equation":
                        explanation_parts.append(f"公式：{leaf.get('latex', '')}")
                    elif leaf_type == "image":
                        figures.append({
                            "path": "",
                            "caption": str(leaf.get("caption", "")),
                            "image_id": str(leaf.get("visual_id", "")),
                        })
                    elif leaf_type == "source_reference":
                        refs = leaf.get("source_refs", {})
                        segment_ids = list(refs.get("segment_ids", [])) if isinstance(refs.get("segment_ids"), list) else []
                        links = list(leaf.get("links", [])) if isinstance(leaf.get("links", []), list) else links
                        start_seconds = float(refs.get("start_seconds", 0) or 0)
                        end_seconds = float(refs.get("end_seconds", start_seconds) or start_seconds)
                        source_label = str(refs.get("label", "") or "")
                        source_url = str(refs.get("url", "") or "")
                points.append({
                    "statement": statement,
                    "explanation": "；".join(part for part in explanation_parts if part),
                    "source_refs": {
                        "segment_ids": segment_ids, "start_seconds": start_seconds,
                        "end_seconds": end_seconds, "label": source_label,
                        "url": source_url, "links": links,
                    },
                    "figures": figures,
                })
            elif child.get("type") == "paragraph" and child.get("text"):
                summary_parts.append(str(child["text"]))
        sections.append({
            "title": str(chapter.get("title", "")),
            "summary": "；".join(summary_parts),
            "knowledge_points": points,
        })
    return sections


def v31_to_legacy_document(document: Mapping[str, Any]) -> dict[str, Any]:
    """只读投影：Document v3.1 → v2 形状（聚合输入/历史诊断用）。"""
    return {
        "schema_version": 2,
        "metadata": dict(document.get("metadata", {})),
        "overview": str(document.get("overview", "")),
        "learning_objectives": list(document.get("learning_objectives", [])),
        "sections": v31_to_legacy_sections(document),
        "figures": [],
        "review": {},
        "render_options": {"include_full_transcript": False},
    }
