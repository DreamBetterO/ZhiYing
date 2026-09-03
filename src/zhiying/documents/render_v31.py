"""V6.1 Document v3.1 原生三端渲染（CP61-2 候选路径，未接生产）。

- Markdown：直接遍历 v3.1 组件树；
- Word：调用 scripts/renderers/render_docx_v31.mjs（公式生成 Word 原生 OMML）；
- PDF：优先 Word 转 PDF，不可用时用同一组件树的 built-in fallback；
- 不注入旧业务模板；只自动加入合同允许的来源/页码/元数据/无障碍字段。
- 生产路径切换（删除 v3_to_v2）在 CP61-5，本模块是独立候选入口。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..utils import ensure_not_cancelled, repair_structured_text_controls, run_cancellable

from ..editorial.document import validate_document_v31, walk_components


def resolve_v31_figures(
    document: Mapping[str, Any],
    workspace_root: Path,
    *,
    project_root: Path | None = None,
) -> dict[str, dict[str, Any]]:
    """从 workspace 的 visual-evidence.json 解析 visual_id → 图片渲染信息。

    返回 {visual_id: {path, width, height, source_label}}；
    只读取已存在 Artifact，不触发 VLM/云。
    """
    workspace_root = workspace_root.resolve()
    allowed_root = Path(project_root).resolve() if project_root is not None else workspace_root
    evidence_path = workspace_root / "knowledge" / "visual-evidence.json"
    figures: dict[str, dict[str, Any]] = {}
    if not evidence_path.is_file():
        return figures
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    for row in payload.get("visual_evidence", []):
        evidence_id = str(row.get("evidence_id", ""))
        image_path = str(row.get("image_path", "") or "")
        if not evidence_id or not image_path:
            continue
        candidate = Path(image_path)
        if not candidate.is_absolute():
            project_candidate = allowed_root / candidate
            workspace_candidate = workspace_root / candidate
            candidate = project_candidate if project_candidate.is_file() else workspace_candidate
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(allowed_root)
        except (FileNotFoundError, OSError, ValueError):
            continue
        if not resolved.is_file():
            continue
        source_timestamp = float(row.get("source_timestamp", 0.0) or 0.0)
        figures[evidence_id] = {
            "path": str(resolved),
            "width": int(row.get("width", 1280) or 1280),
            "height": int(row.get("height", 720) or 720),
            "source_label": str(row.get("timestamp_label", "") or _hhmmss(source_timestamp)),
        }
    return figures


def _hhmmss(seconds: float) -> str:
    total = max(0, int(seconds))
    return f"{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}"


def _component_text(component: Mapping[str, Any]) -> str:
    return str(component.get("text", "") or component.get("title", "") or "")


# ---------------------------------------------------------------------------
# Markdown 原生 v3.1
# ---------------------------------------------------------------------------

def render_markdown_v31(
    document: Mapping[str, Any],
    output: Path,
    *,
    figure_map: Mapping[str, Mapping[str, Any]] | None = None,
    project_root: Path | None = None,
) -> Path:
    figure_map = dict(figure_map or {})
    project_root = Path(project_root or Path.cwd())
    validate_document_v31(document)
    lines: list[str] = []

    def image_path(component: Mapping[str, Any]) -> str:
        entry = figure_map.get(str(component.get("visual_id", "")))
        if not entry:
            return str(component.get("visual_id", ""))
        path_value = str(entry.get("path", ""))
        if os.path.isabs(path_value):
            try:
                return Path(path_value).resolve().relative_to(project_root.resolve()).as_posix()
            except ValueError:
                return Path(path_value).name
        return path_value

    def walk(components: Iterable[Mapping[str, Any]], level: int = 2) -> None:
        for component in components:
            ctype = component.get("type")
            if ctype == "heading":
                depth = min(6, max(1, int(component.get("level", level))))
                lines.append(f"{'#' * depth} {_component_text(component)}")
                lines.append("")
            elif ctype == "paragraph":
                if component.get("text"):
                    lines.append(str(component["text"]))
                    lines.append("")
            elif ctype == "list":
                for item in component.get("items", []):
                    lines.append(f"- {item}")
                lines.append("")
            elif ctype == "equation":
                latex = str(component.get("latex", ""))
                if latex:
                    lines.extend(["$$", latex, "$$", ""])
            elif ctype == "image":
                caption = str(component.get("caption", ""))
                lines.append(f"![{caption}]({image_path(component)})")
                lines.append("")
                if caption:
                    lines.extend([f"*{caption}*", ""])
            elif ctype == "callout":
                if component.get("text"):
                    lines.append(f"> {component['text']}")
                    lines.append("")
            elif ctype == "page_break":
                lines.append("---")
                lines.append("")
            elif ctype == "source_reference":
                for source in component.get("links", []) if isinstance(component.get("links"), list) else []:
                    label = str(source.get("label", ""))
                    url = str(source.get("url", ""))
                    if url:
                        lines.append(f"[▶ 回看来源 · {label}]({url})")
                        lines.append("")
            elif ctype == "container":
                walk(component.get("children", []), level + 1)
            elif ctype == "table":
                for row in component.get("rows", []):
                    if isinstance(row, list):
                        lines.append(" | ".join(str(cell) for cell in row))
                    else:
                        lines.append(str(row))
                lines.append("")

    walk(document.get("components", []))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="utf-8")
    return output


# ---------------------------------------------------------------------------
# Word 原生 v3.1（node + docx Math → OMML）
# ---------------------------------------------------------------------------

def render_docx_v31(
    document_json: Path,
    output: Path,
    *,
    project_root: Path,
    figure_map: Mapping[str, Mapping[str, Any]] | None = None,
    cancel_check=None,
) -> Path:
    script = Path(__file__).resolve().parents[3] / "scripts" / "renderers" / "render_docx_v31.mjs"
    figures_path = output.with_suffix(".figures.json")
    safe_document_path = output.with_suffix(".render-safe.json")
    figures_path.parent.mkdir(parents=True, exist_ok=True)
    document = json.loads(document_json.read_text(encoding="utf-8"))
    safe_document_path.write_text(
        json.dumps(repair_structured_text_controls(document), ensure_ascii=False),
        encoding="utf-8",
    )
    figures_path.write_text(
        json.dumps({key: dict(value) for key, value in dict(figure_map or {}).items()}, ensure_ascii=False),
        encoding="utf-8",
    )
    try:
        run_cancellable(
            [
                "node", str(script), str(safe_document_path), str(output),
                str(project_root.resolve()), str(figures_path),
            ],
            cancel_check=cancel_check or (lambda: False),
            timeout_seconds=120.0,
        )
    finally:
        figures_path.unlink(missing_ok=True)
        safe_document_path.unlink(missing_ok=True)
    if not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError(f"Word v3.1 渲染输出缺失：{output.name}")
    _normalize_docx_for_replay(output)
    _validate_docx_package(output)
    return output


def _validate_docx_package(output: Path) -> None:
    """验证 Word 包及其中所有 XML，避免把不可打开的半成品标记为成功。"""
    import xml.etree.ElementTree as ET
    import zipfile

    try:
        with zipfile.ZipFile(output, "r") as archive:
            corrupt = archive.testzip()
            if corrupt:
                raise RuntimeError(f"Word 包含损坏条目：{corrupt}")
            required = {"[Content_Types].xml", "_rels/.rels", "word/document.xml"}
            missing = sorted(required.difference(archive.namelist()))
            if missing:
                raise RuntimeError(f"Word 包缺少必要条目：{', '.join(missing)}")
            for name in archive.namelist():
                if name.endswith(".xml"):
                    ET.fromstring(archive.read(name))
    except (zipfile.BadZipFile, ET.ParseError, OSError) as exc:
        raise RuntimeError(f"Word 文档结构无效：{exc}") from exc


def _normalize_docx_for_replay(output: Path) -> None:
    """移除 docx 包内运行时钟差异，保证相同 Artifact 可字节级 replay。"""
    import re
    import zipfile

    fixed_time = (1980, 1, 1, 0, 0, 0)
    temporary = output.with_suffix(output.suffix + ".normalized")
    with zipfile.ZipFile(output, "r") as source:
        entries = {name: source.read(name) for name in source.namelist()}

    # docx 为外部链接生成随机 relationship id；按关系文件中的稳定顺序重编号，
    # 并同步替换所属 XML 的引用。
    for rel_name in [name for name in entries if name.endswith(".rels")]:
        rel_text = entries[rel_name].decode("utf-8")
        ids = re.findall(r'\bId="([^"]+)"', rel_text)
        if not ids:
            continue
        source_name = None
        if "/_rels/" in rel_name:
            prefix, filename = rel_name.split("/_rels/", 1)
            source_name = f"{prefix}/{filename[:-5]}"
        source_text = entries[source_name].decode("utf-8") if source_name in entries else None
        placeholders = {old: f"__V31_REL_{index:04d}__" for index, old in enumerate(ids, start=1)}
        for old, placeholder in placeholders.items():
            rel_text = rel_text.replace(f'"{old}"', f'"{placeholder}"')
            if source_text is not None:
                source_text = source_text.replace(f'"{old}"', f'"{placeholder}"')
        for index, placeholder in enumerate(placeholders.values(), start=1):
            rel_text = rel_text.replace(placeholder, f"rId{index}")
            if source_text is not None:
                source_text = source_text.replace(placeholder, f"rId{index}")
        entries[rel_name] = rel_text.encode("utf-8")
        if source_name is not None and source_text is not None:
            entries[source_name] = source_text.encode("utf-8")

    with zipfile.ZipFile(
        temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6,
    ) as target:
        for name, payload in entries.items():
            if name == "docProps/core.xml":
                text = payload.decode("utf-8")
                text = re.sub(
                    r"(<dcterms:(?:created|modified)[^>]*>)[^<]*(</dcterms:(?:created|modified)>)",
                    r"\g<1>1980-01-01T00:00:00.000Z\g<2>", text,
                )
                payload = text.encode("utf-8")
            info = zipfile.ZipInfo(name, fixed_time)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            target.writestr(info, payload)
    temporary.replace(output)


# ---------------------------------------------------------------------------
# PDF：优先 Word 转 PDF；built-in fallback 直接消费组件树
# ---------------------------------------------------------------------------

def render_pdf_v31(
    document: Mapping[str, Any],
    word: Path,
    output: Path,
    *,
    figure_map: Mapping[str, Mapping[str, Any]] | None = None,
    cancel_check=None,
) -> str:
    """优先使用本机 Word 导出（OMML 保真）；不可用时 built-in v3.1 fallback。"""
    from ..render import _word_export_to_pdf
    if word.is_file() and word.stat().st_size > 0 and _word_export_to_pdf(word, output, cancel_check=cancel_check):
        return "local_word"
    render_pdf_fallback_v31(document, output, figure_map=figure_map, cancel_check=cancel_check)
    return "built_in_v31"


def render_pdf_fallback_v31(
    document: Mapping[str, Any],
    output: Path,
    *,
    figure_map: Mapping[str, Mapping[str, Any]] | None = None,
    cancel_check=None,
) -> Path:
    """reportlab 直接消费 v3.1 组件树；公式以文本呈现（能力差异在 QualityReport 记录）。"""
    validate_document_v31(document)
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Image as ReportLabImage, Paragraph, SimpleDocTemplate, Spacer
    from reportlab.lib.styles import getSampleStyleSheet

    font_name = "Helvetica"
    for candidate in (
        Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "msyh.ttc",
        Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "simhei.ttf",
    ):
        if candidate.is_file():
            try:
                pdfmetrics.registerFont(TTFont("MSYH", str(candidate)))
                font_name = "MSYH"
                break
            except Exception:
                pass
    base = getSampleStyleSheet()
    body = ParagraphStyle("BodyV31", parent=base["Normal"], fontName=font_name, fontSize=10.5, leading=16, textColor=colors.HexColor("#243342"))
    heading1 = ParagraphStyle("H1V31", parent=base["Heading1"], fontName=font_name, fontSize=15, leading=20, textColor=colors.HexColor("#17324D"), spaceBefore=14, spaceAfter=6)
    heading2 = ParagraphStyle("H2V31", parent=base["Heading2"], fontName=font_name, fontSize=12.5, leading=17, textColor=colors.HexColor("#17324D"), spaceBefore=10, spaceAfter=4)
    heading3 = ParagraphStyle("H3V31", parent=base["Heading3"], fontName=font_name, fontSize=11, leading=15, textColor=colors.HexColor("#167D87"), spaceBefore=8, spaceAfter=3)
    callout = ParagraphStyle("CalloutV31", parent=body, leftIndent=12, borderColor=colors.HexColor("#B0C4DE"), borderWidth=0, spaceBefore=4, spaceAfter=8)
    caption = ParagraphStyle("CaptionV31", parent=body, alignment=TA_CENTER, fontSize=9, textColor=colors.HexColor("#667786"))

    title = str(document.get("metadata", {}).get("document_title", "") or document.get("metadata", {}).get("title", ""))
    story: list[Any] = []
    figure_map = dict(figure_map or {})
    if title:
        story.append(Paragraph(title, heading1))

    def escape(text: str) -> str:
        return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def latex_to_readable(text: str) -> str:
        import re

        symbols = {
            "cdot": "·", "times": "×", "le": "≤", "leq": "≤", "ge": "≥", "geq": "≥",
            "neq": "≠", "sum": "Σ", "prod": "Π", "infty": "∞", "to": "→", "pm": "±",
            "ln": "ln", "exp": "exp", "left": "", "right": "",
        }

        def read_group(source: str, start: int) -> tuple[str, int] | None:
            if start >= len(source) or source[start] != "{":
                return None
            depth = 0
            for position in range(start, len(source)):
                if source[position] == "{":
                    depth += 1
                elif source[position] == "}":
                    depth -= 1
                    if depth == 0:
                        return source[start + 1:position], position + 1
            return None

        source = str(text)
        result: list[str] = []
        index = 0
        while index < len(source):
            if source[index] == "\\":
                command = re.match(r"\\([A-Za-z]+)", source[index:])
                if command:
                    name = command.group(1)
                    next_index = index + len(name) + 1
                    if name == "frac":
                        numerator = read_group(source, next_index)
                        denominator = read_group(source, numerator[1]) if numerator else None
                        if numerator and denominator:
                            result.append(
                                f"({latex_to_readable(numerator[0])})/({latex_to_readable(denominator[0])})"
                            )
                            index = denominator[1]
                            continue
                    result.append(symbols.get(name, name))
                    index = next_index
                    continue
                if source.startswith(r"\,", index):
                    result.append(" ")
                    index += 2
                    continue
            if source[index] not in "{}":
                result.append(source[index])
            index += 1
        return "".join(result)

    def inline_markup(text: str, *, bold: bool = False) -> str:
        source = str(text or "")
        parts: list[str] = []
        index = 0

        def formatted(value: str) -> str:
            escaped = escape(value)
            return f"<b>{escaped}</b>" if bold and escaped else escaped

        while index < len(source):
            bold_at = source.find("**", index)
            math_at = source.find("$", index)
            candidates = [value for value in (bold_at, math_at) if value >= 0]
            if not candidates:
                parts.append(formatted(source[index:]))
                break
            marker_at = min(candidates)
            parts.append(formatted(source[index:marker_at]))
            if marker_at == bold_at:
                end = source.find("**", marker_at + 2)
                if end < 0:
                    parts.append(formatted(source[marker_at:]))
                    break
                parts.append(inline_markup(source[marker_at + 2:end], bold=True))
                index = end + 2
                continue
            delimiter = "$$" if source.startswith("$$", marker_at) else "$"
            end = source.find(delimiter, marker_at + len(delimiter))
            if end < 0:
                parts.append(formatted(source[marker_at:]))
                break
            latex = source[marker_at + len(delimiter):end].strip()
            readable = escape(latex_to_readable(latex))
            parts.append(f"<b>{readable}</b>" if bold and readable else readable)
            index = end + len(delimiter)
        return "".join(parts)

    def walk(components: Iterable[Mapping[str, Any]], level: int = 2) -> None:
        for component in components:
            ctype = component.get("type")
            if ctype == "heading":
                depth = int(component.get("level", level))
                style = heading1 if depth <= 1 else heading2 if depth == 2 else heading3
                story.append(Paragraph(escape(_component_text(component)), style))
            elif ctype == "paragraph":
                if component.get("text"):
                    story.append(Paragraph(inline_markup(component["text"]), body))
            elif ctype == "list":
                for item in component.get("items", []):
                    story.append(Paragraph(f"•  {inline_markup(item)}", body))
            elif ctype == "equation":
                story.append(Paragraph(f"[公式] {escape(latex_to_readable(component.get('latex', '')))}", body))
            elif ctype == "image":
                entry = figure_map.get(str(component.get("visual_id", "")))
                image_path = Path(str(entry.get("path", ""))) if entry else None
                if image_path is not None and image_path.is_file():
                    figure = ReportLabImage(str(image_path))
                    max_width, max_height = 166 * mm, 100 * mm
                    scale = min(max_width / figure.imageWidth, max_height / figure.imageHeight, 1.0)
                    figure.drawWidth = figure.imageWidth * scale
                    figure.drawHeight = figure.imageHeight * scale
                    figure.hAlign = "CENTER"
                    story.append(figure)
                if component.get("caption"):
                    story.append(Paragraph(escape(component["caption"]), caption))
            elif ctype == "callout":
                if component.get("text"):
                    story.append(Paragraph(inline_markup(component["text"]), callout))
            elif ctype == "page_break":
                story.append(Spacer(1, 6 * mm))
            elif ctype == "container":
                walk(component.get("children", []), level + 1)

    walk(document.get("components", []))
    output.parent.mkdir(parents=True, exist_ok=True)
    ensure_not_cancelled(cancel_check)
    SimpleDocTemplate(
        str(output), pagesize=A4, rightMargin=22 * mm, leftMargin=22 * mm,
        topMargin=18 * mm, bottomMargin=18 * mm, title=title, invariant=1,
    ).build(story)
    ensure_not_cancelled(cancel_check)
    return output


# ---------------------------------------------------------------------------
# 跨格式统计（供 QualityReport / render.verify 使用）
# ---------------------------------------------------------------------------

def count_word_omml(docx: Path) -> int:
    """统计 docx 内 OMML 公式段数（<m:oMath> 开标签，一个公式一段）。"""
    import zipfile
    try:
        with zipfile.ZipFile(docx) as archive:
            document_xml = archive.read("word/document.xml").decode("utf-8")
    except (zipfile.BadZipFile, KeyError, OSError):
        return 0
    return len(__import__("re").findall(r"<m:oMath>", document_xml))


def component_statistics(document: Mapping[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for component in walk_components(document.get("components", [])):
        ctype = str(component.get("type", ""))
        counts[ctype] = counts.get(ctype, 0) + 1
    counts["equations"] = counts.get("equation", 0)
    counts["images"] = counts.get("image", 0)
    return counts


def markdown_contains_absolute_path(markdown: Path) -> list[str]:
    """返回 Markdown 中泄露本地绝对路径/工作区内部路径的图片行。"""
    import re
    text = markdown.read_text(encoding="utf-8")
    leaked = []
    for line in text.splitlines():
        if re.search(r"!\[[^\]]*\]\([A-Za-z]:[\\/]", line) or "/workspace/" in line:
            leaked.append(line)
    return leaked
