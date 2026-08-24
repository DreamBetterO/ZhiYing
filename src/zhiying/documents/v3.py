from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping


CAPABILITY_MANIFEST = {
    "components": [
        "heading", "paragraph", "list", "equation", "table", "image",
        "container", "callout", "page_break", "source_reference",
    ],
    "layout_hints": ["full_width", "image_text", "two_column", "compact_table"],
    "unknown_component_fallback": "paragraph",
}


def build_document_plan(document_v2: Mapping[str, Any], *, mode: str = "local") -> dict[str, Any]:
    chapters = []
    for index, section in enumerate(document_v2.get("sections", []), start=1):
        chapters.append({
            "chapter_id": f"chapter_{index:03d}",
            "title": str(section.get("title") or f"章节 {index}"),
            "layout_hint": "full_width",
            "source_section": deepcopy(dict(section)),
        })
    return {
        "schema_version": 1,
        "mode": "cloud" if mode == "cloud" else "local",
        "capability_manifest": deepcopy(CAPABILITY_MANIFEST),
        "metadata": deepcopy(dict(document_v2.get("metadata", {}))),
        "overview": str(document_v2.get("overview", "")),
        "learning_objectives": list(document_v2.get("learning_objectives", [])),
        "chapters": chapters,
        "source_document": {
            key: deepcopy(document_v2.get(key))
            for key in ("mode", "model", "model_attempts", "cloud_usage", "knowledge_pipeline", "notice", "review", "transcript")
            if key in document_v2
        },
    }


def validate_document_plan(plan: Mapping[str, Any]) -> None:
    if int(plan.get("schema_version", 0)) != 1:
        raise ValueError("DocumentPlan schema_version 无效")
    capabilities = plan.get("capability_manifest", {})
    if capabilities.get("components") != CAPABILITY_MANIFEST["components"]:
        raise ValueError("DocumentPlan renderer capability 不匹配")
    ids = [str(row.get("chapter_id", "")) for row in plan.get("chapters", [])]
    if not ids or any(not item for item in ids) or len(ids) != len(set(ids)):
        raise ValueError("DocumentPlan chapter_id 必须稳定且唯一")


def compose_chapters(plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    validate_document_plan(plan)
    chapters = []
    for chapter in plan["chapters"]:
        section = dict(chapter["source_section"])
        children: list[dict[str, Any]] = [{
            "type": "heading", "level": 2, "text": str(section.get("title", "")),
        }]
        if section.get("summary"):
            children.append({"type": "paragraph", "text": str(section["summary"])})
        for point_index, point in enumerate(section.get("knowledge_points", []), start=1):
            point_id = f"{chapter['chapter_id']}.point_{point_index:03d}"
            point_children = [{
                "type": "heading", "level": 3, "text": str(point.get("statement", "")),
            }]
            content_blocks = list(point.get("content_blocks", []))
            for block in content_blocks:
                block_type = str(block.get("type", "paragraph"))
                text = str(block.get("text", ""))
                items = [str(item) for item in block.get("items", []) if str(item).strip()]
                if block_type in {"paragraph", "visual_lead_in", "visual_takeaway"} and text:
                    point_children.append({"type": "paragraph", "text": text, "source_block_id": str(block.get("block_id", ""))})
                elif block_type in {"rule_list", "detail_list", "step_list", "example_list", "condition_list", "pitfall_list"}:
                    point_children.append({"type": "list", "items": items or ([text] if text else []), "source_block_id": str(block.get("block_id", ""))})
                elif block_type in {"formula", "equation"} and text:
                    point_children.append({"type": "equation", "latex": text, "text": text, "source_block_id": str(block.get("block_id", ""))})
                elif block_type in {"understanding_tip", "editorial_note", "review_tip"} and text:
                    point_children.append({"type": "callout", "style": block_type, "text": text, "source_block_id": str(block.get("block_id", ""))})
                elif text:
                    point_children.append({"type": "paragraph", "text": text, "fallback_from": block_type, "source_block_id": str(block.get("block_id", ""))})
            if not content_blocks and point.get("explanation"):
                point_children.append({"type": "paragraph", "text": str(point["explanation"])})
            for field, title in (("details", "细节"), ("steps", "步骤"), ("examples", "示例"), ("conditions", "条件"), ("pitfalls", "易错点")) if not content_blocks else ():
                items = [str(item) for item in point.get(field, []) if str(item).strip()]
                if items:
                    point_children.append({"type": "list", "title": title, "items": items})
            for figure in point.get("figures", []):
                if figure.get("path"):
                    point_children.append({
                        "type": "image", "path": str(figure["path"]),
                        "caption": str(figure.get("caption", "")),
                        "source_refs": deepcopy(point.get("source_refs", {})),
                    })
            point_children.append({
                "type": "source_reference",
                "source_refs": deepcopy(point.get("source_refs", {})),
                "links": deepcopy(point.get("source_links", [])),
            })
            for component in point_children:
                if component.get("type") not in {"heading", "source_reference"}:
                    component.setdefault("source_refs", deepcopy(point.get("source_refs", {})))
            children.append({
                "type": "container", "semantic_role": "knowledge_point",
                "component_id": point_id, "statement": str(point.get("statement", "")),
                "source_point": deepcopy(dict(point)), "children": point_children,
            })
        chapters.append({
            "type": "container", "semantic_role": "chapter",
            "component_id": str(chapter["chapter_id"]),
            "layout_hint": str(chapter.get("layout_hint", "full_width")),
            "title": str(section.get("title", "")), "summary": str(section.get("summary", "")),
            "children": children,
        })
    return chapters


def validate_chapters(chapters: list[Mapping[str, Any]]) -> list[dict[str, str]]:
    issues = []
    ids: set[str] = set()
    for chapter in chapters:
        component_id = str(chapter.get("component_id", ""))
        if not component_id or component_id in ids:
            issues.append({"component_id": component_id, "code": "DUPLICATE_OR_EMPTY_ID"})
        ids.add(component_id)
        if not str(chapter.get("title", "")).strip():
            issues.append({"component_id": component_id, "code": "EMPTY_TITLE"})
    return issues


def repair_chapters(chapters: list[dict[str, Any]], issues: list[Mapping[str, str]]) -> list[dict[str, Any]]:
    repaired = deepcopy(chapters)
    empty_titles = {row.get("component_id") for row in issues if row.get("code") == "EMPTY_TITLE"}
    for index, chapter in enumerate(repaired, start=1):
        if chapter.get("component_id") in empty_titles:
            chapter["title"] = f"章节 {index}"
            if chapter.get("children") and chapter["children"][0].get("type") == "heading":
                chapter["children"][0]["text"] = chapter["title"]
    return repaired


def compile_document_v3(plan: Mapping[str, Any], chapters: list[dict[str, Any]]) -> dict[str, Any]:
    issues = validate_chapters(chapters)
    if issues:
        raise ValueError(f"章节仍未通过校验：{issues}")
    source = dict(plan.get("source_document", {}))
    return {
        "schema_version": 3,
        "contract_version": "document-v3",
        "metadata": deepcopy(dict(plan.get("metadata", {}))),
        "overview": str(plan.get("overview", "")),
        "learning_objectives": list(plan.get("learning_objectives", [])),
        "components": deepcopy(chapters),
        "renderer_capability_manifest": deepcopy(CAPABILITY_MANIFEST),
        **source,
    }


def validate_document_v3(document: Mapping[str, Any]) -> None:
    if int(document.get("schema_version", 0)) != 3:
        raise ValueError("Document 必须为 v3")
    if not isinstance(document.get("components"), list) or not document["components"]:
        raise ValueError("Document v3 缺少 components")
    allowed = set(CAPABILITY_MANIFEST["components"])

    def visit(component: Mapping[str, Any]) -> None:
        if component.get("type") not in allowed:
            raise ValueError(f"未知 Document v3 component：{component.get('type')}")
        for child in component.get("children", []):
            visit(child)

    for component in document["components"]:
        visit(component)


def v3_to_v2(document: Mapping[str, Any]) -> dict[str, Any]:
    """Renderer/read adapter only; v3 remains the stored authority."""
    if str(document.get("contract_version", "")) == "document-v3.1":
        from ..editorial.document import v31_to_legacy_document
        legacy = v31_to_legacy_document(document)
        defaults = {
            "notice": "", "mode": "", "model": "", "model_attempts": [],
            "cloud_usage": {}, "knowledge_pipeline": {},
        }
        for key, default in defaults.items():
            legacy[key] = deepcopy(document.get(key, default))
        return legacy
    validate_document_v3(document)
    sections = []
    for chapter in document["components"]:
        points = [
            deepcopy(child.get("source_point", {}))
            for child in chapter.get("children", [])
            if child.get("semantic_role") == "knowledge_point"
        ]
        sections.append({
            "title": str(chapter.get("title", "")),
            "summary": str(chapter.get("summary", "")),
            "knowledge_points": points,
        })
    return {
        "schema_version": 2,
        "metadata": deepcopy(dict(document.get("metadata", {}))),
        "overview": str(document.get("overview", "")),
        "learning_objectives": list(document.get("learning_objectives", [])),
        "sections": sections,
        "figures": [],
        "transcript": deepcopy(document.get("transcript", [])),
        "review": deepcopy(document.get("review", {})),
        "notice": str(document.get("notice", "")),
        "mode": str(document.get("mode", "")),
        "model": str(document.get("model", "")),
        "model_attempts": deepcopy(document.get("model_attempts", [])),
        "cloud_usage": deepcopy(document.get("cloud_usage", {})),
        "knowledge_pipeline": deepcopy(document.get("knowledge_pipeline", {})),
        "render_options": {"include_full_transcript": True},
    }
