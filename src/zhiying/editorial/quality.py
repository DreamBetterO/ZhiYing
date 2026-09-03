"""V6.1 QualityReport v2 与 PageAuditReport v1（CP61-2）。

质量门（目标合同 quality_gates）：
- intent：forbidden 命中=0、required 全满足；
- evidence：事实组件必须有 source_refs；无来源补充进入正文；
- math：equation 组件数、Word OMML 数一致；latex 缺失即 issue；
- visual：image 必须有 role/caption/alt_text；
- page：基于组件树的静态分页风险（孤行/连续 page_break 等）；
- render：Markdown 无绝对路径泄露、跨格式公式 parity。

返回结构为稳定 JSON（QualityReport v2 / PageAuditReport v1），可落盘。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable, Mapping

from .document import walk_components
from .policy import EditorialPolicy

QUALITY_REPORT_VERSION = 2
PAGE_AUDIT_VERSION = 1

_HARD_SEVERITIES = ("error",)

_FIXED_NUMBERING_PATTERN = re.compile(r"^\d{1,2}\s*[·.、．]\s*\S|^第\s*\d+\s*[章节讲]")


def _factual_types() -> tuple[str, ...]:
    return ("paragraph", "list", "equation", "table", "callout")


def audit_document_v31(
    document: Mapping[str, Any],
    policy: EditorialPolicy | None = None,
) -> dict[str, Any]:
    """内容级质量审计 → QualityReport v2（不渲染）。"""
    issues: list[dict[str, Any]] = []
    stats: dict[str, int] = {}
    actual_chars = 0
    for component in walk_components(document.get("components", [])):
        ctype = str(component.get("type", ""))
        stats[ctype] = stats.get(ctype, 0) + 1
        component_id = str(component.get("component_id", "")) or ctype
        actual_chars += len(str(component.get("text", "") or component.get("latex", "")))
        actual_chars += sum(len(str(item)) for item in component.get("items", []) if item is not None)

        if ctype in _factual_types() and not component.get("source_refs"):
            issues.append({
                "code": "EVIDENCE_NO_SOURCE", "severity": "error",
                "owner_component": component_id, "detail": "事实组件缺少 source_refs",
            })
        if ctype == "equation" and not str(component.get("latex", "")).strip():
            issues.append({
                "code": "MATH_LATEX_MISSING", "severity": "error",
                "owner_component": component_id, "detail": "equation 组件缺少 latex",
            })
        if ctype == "image":
            for field in ("role", "caption", "alt_text"):
                if not component.get(field):
                    issues.append({
                        "code": "VISUAL_FIELD_MISSING", "severity": "error",
                        "owner_component": component_id, "detail": f"image 缺少 {field}",
                    })

    target_chars = max(0, int(document.get("provenance", {}).get("target_chars", 0) or 0))
    available_chars = max(0, int(document.get("provenance", {}).get("available_content_chars", 0) or 0))
    expected_chars = min(target_chars, available_chars) if target_chars and available_chars else 0
    # 小型单元/夹具不做产量门；课程级材料达到 500 字后才判断显著欠产。
    minimum_chars = int(expected_chars * 0.55) if expected_chars >= 500 else 0
    if minimum_chars and actual_chars < minimum_chars:
        issues.append({
            "code": "CONTENT_UNDER_TARGET", "severity": "error",
            "owner_component": "document", "detail": (
                f"正文产量 {actual_chars} 字，低于可用材料基线 {expected_chars} 字的 55% 下限"
            ),
        })

    intent: dict[str, Any] = {"forbidden_hits": [], "required_missing": []}
    if policy is not None:
        intent["forbidden_hits"] = policy.forbidden_hits(document.get("components", []))
        intent["required_missing"] = policy.required_missing(document.get("components", []))
        for hit in intent["forbidden_hits"]:
            issues.append({
                "code": "INTENT_FORBIDDEN_HIT", "severity": "error",
                "owner_component": f"policy:{hit}", "detail": f"命中用户 forbidden 约束：{hit}",
            })
        for missing in intent["required_missing"]:
            issues.append({
                "code": "INTENT_REQUIRED_MISSING", "severity": "error",
                "owner_component": f"policy:{missing}", "detail": f"缺少 required 约束：{missing}",
            })

    hard_issues = [row for row in issues if row["severity"] in _HARD_SEVERITIES]
    return {
        "schema_version": QUALITY_REPORT_VERSION,
        "status": "valid" if not hard_issues else "invalid",
        "issues": issues,
        "intent": intent,
        "math": {
            "equation_components": stats.get("equation", 0),
            "word_omml": None,  # 渲染后由 audit_render_outputs 回填
        },
        "visual": {
            "image_components": stats.get("image", 0),
        },
        "evidence": {
            "source_reference_components": stats.get("source_reference", 0),
        },
        "content": {
            "actual_chars": actual_chars,
            "target_chars": target_chars,
            "available_content_chars": available_chars,
            "expected_chars": expected_chars,
            "minimum_chars": minimum_chars,
        },
        "statistics": stats,
    }


def audit_render_outputs(
    document: Mapping[str, Any],
    *,
    markdown: Path | None = None,
    docx: Path | None = None,
    pdf: Path | None = None,
    pdf_mode: str | None = None,
) -> dict[str, Any]:
    """渲染产物审计：OMML 与 equation 一致、Markdown 无绝对路径、公式 parity。"""
    from ..documents.render_v31 import count_word_omml

    issues: list[dict[str, Any]] = []
    def count_inline_math(text: str) -> int:
        source = str(text or "")
        count = 0
        index = 0
        while index < len(source):
            bold_at = source.find("**", index)
            math_at = source.find("$", index)
            candidates = [value for value in (bold_at, math_at) if value >= 0]
            if not candidates:
                break
            marker_at = min(candidates)
            if marker_at == bold_at:
                end = source.find("**", marker_at + 2)
                if end < 0:
                    break
                count += count_inline_math(source[marker_at + 2:end])
                index = end + 2
                continue
            delimiter = "$$" if source.startswith("$$", marker_at) else "$"
            end = source.find(delimiter, marker_at + len(delimiter))
            if end < 0:
                break
            if source[marker_at + len(delimiter):end].strip():
                count += 1
            index = end + len(delimiter)
        return count

    equation_components = 0
    inline_math_expressions = 0
    for component in walk_components(document.get("components", [])):
        component_type = component.get("type")
        if component_type == "equation":
            equation_components += 1
        elif component_type in {"paragraph", "callout"}:
            inline_math_expressions += count_inline_math(str(component.get("text", "")))
        elif component_type == "list":
            inline_math_expressions += sum(
                count_inline_math(str(item)) for item in component.get("items", [])
            )
    stats = {
        "equation_components": equation_components,
        "inline_math_expressions": inline_math_expressions,
        "expected_word_omml": equation_components + inline_math_expressions,
    }

    omml_count = None
    if docx is not None and docx.is_file() and docx.stat().st_size > 0:
        omml_count = count_word_omml(docx)
        stats["word_omml"] = omml_count
        if omml_count != stats["expected_word_omml"]:
            issues.append({
                "code": "MATH_OMML_MISMATCH", "severity": "error",
                "owner_component": "render.word", "detail": (
                    f"Word OMML({omml_count}) != 预期公式数({stats['expected_word_omml']})"
                ),
            })

    if markdown is not None and markdown.is_file():
        from ..documents.render_v31 import markdown_contains_absolute_path
        leaked = markdown_contains_absolute_path(markdown)
        stats["markdown_absolute_path_leaks"] = len(leaked)
        if leaked:
            issues.append({
                "code": "MARKDOWN_ABSOLUTE_PATH", "severity": "error",
                "owner_component": "render.markdown", "detail": f"泄露本地绝对路径 {len(leaked)} 处",
            })
        stats["markdown_equation_blocks"] = len(re.findall(
            r"^\$\$[^$]*\$\$", markdown.read_text(encoding="utf-8"), re.MULTILINE,
        ))

    if pdf is not None and (not pdf.is_file() or pdf.stat().st_size == 0):
        issues.append({
            "code": "PDF_MISSING", "severity": "error",
            "owner_component": "render.pdf", "detail": "PDF 输出缺失或为空",
        })
    stats["pdf_mode"] = pdf_mode

    return {
        "schema_version": QUALITY_REPORT_VERSION,
        "status": "valid" if not issues else "invalid",
        "issues": issues,
        "statistics": stats,
    }


def build_page_audit_report(document: Mapping[str, Any]) -> dict[str, Any]:
    """基于组件树的静态页面审计 → PageAuditReport v1（不渲染截图）。"""
    components = list(walk_components(document.get("components", [])))
    issues: list[dict[str, Any]] = []
    stats = {
        "page_break_components": sum(1 for row in components if row.get("type") == "page_break"),
        "heading_components": sum(1 for row in components if row.get("type") == "heading"),
        "paragraph_components": sum(1 for row in components if row.get("type") == "paragraph"),
        "list_components": sum(1 for row in components if row.get("type") == "list"),
        "table_components": sum(1 for row in components if row.get("type") == "table"),
        "image_components": sum(1 for row in components if row.get("type") == "image"),
        "equation_components": sum(1 for row in components if row.get("type") == "equation"),
        "source_reference_components": sum(1 for row in components if row.get("type") == "source_reference"),
    }
    # 孤行风险：章节最后一个子组件是 heading（标题后无正文）
    for component in document.get("components", []):
        if component.get("type") == "container" and component.get("semantic_role") == "chapter":
            children = list(component.get("children", []))
            if children and children[-1].get("type") in {"heading", "page_break"}:
                issues.append({
                    "code": "PAGE_ORPHAN_HEADING", "severity": "warning",
                    "owner_component": str(component.get("component_id", "")),
                    "detail": "章节末尾为标题/分页，存在孤行风险",
                })
    # 连续 page_break 检查
    for index, row in enumerate(components[:-1]):
        if row.get("type") == "page_break" and components[index + 1].get("type") == "page_break":
            issues.append({
                "code": "PAGE_DOUBLE_BREAK", "severity": "warning",
                "owner_component": str(row.get("component_id", "")),
                "detail": "连续 page_break",
            })
    # 固定编号残留检查（renderer 不得注入）
    for row in components:
        title = str(row.get("title", "") or row.get("text", "") or "")
        if _FIXED_NUMBERING_PATTERN.match(title):
            issues.append({
                "code": "PAGE_FIXED_NUMBERING", "severity": "error",
                "owner_component": str(row.get("component_id", "")),
                "detail": f"检测到固定编号样式：{title[:30]}",
            })
    return {
        "schema_version": PAGE_AUDIT_VERSION,
        "status": "valid" if not [row for row in issues if row["severity"] == "error"] else "invalid",
        "issues": issues,
        "statistics": stats,
    }
