from __future__ import annotations

import html
import json
import shutil
import subprocess
from pathlib import Path
from urllib.parse import quote

from .summarize import merge_transcript_segments
from .runtime import bundled_path, find_tool
from .utils import hhmmss

_POINT_LIST_FIELDS = (
    ("details", "补充细节"), ("steps", "步骤"), ("examples", "课程案例"),
    ("conditions", "适用条件与边界"), ("pitfalls", "易错点"),
)


def render_markdown(document: dict, output: Path, include_transcript: bool = True) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    document_title = document["metadata"].get("document_title") or document["metadata"]["title"]
    lines = [
        f"# {document_title}", "",
        f"> {document['notice']}", "",
        f"**视频时长**：{document['metadata']['duration_label']}  ",
        f"**整理方式**：{'多视频智能聚合' if document['mode'] == 'cloud_aggregate' else 'Qwen 智能整理' if document['mode'] == 'cloud_summary' else '离线提取'}", "",
        "## 内容导览", "", document["overview"], "",
    ]
    if document.get("learning_objectives"):
        lines.extend(["## 学习目标", ""])
        lines.extend(f"- {item}" for item in document["learning_objectives"])
        lines.append("")
    figures = document.get("figures", [])
    if not document.get("sections") and figures:
        lines.extend(["## 关键画面", ""])
        for figure in figures:
            image_path = Path(figure["path"]).resolve()
            lines.extend([f"![{figure['caption']}]({image_path.as_posix()})", "", f"*{figure['caption']}*", ""])
    for section_index, section in enumerate(document.get("sections", []), start=1):
        lines.extend([f"## {section_index:02d} · {section['title']}", "", f"> {section.get('summary', '')}", ""])
        for point_index, point in enumerate(section.get("knowledge_points", []), start=1):
            lines.append(f"### {section_index}.{point_index} {point['statement']}")
            lines.append("")
            if point.get("explanation"):
                lines.append(point['explanation'])
                lines.append("")
            for field, label in _POINT_LIST_FIELDS:
                values = point.get(field) or []
                if values:
                    lines.extend([f"**{label}**", ""])
                    lines.extend(f"- {item}" for item in values)
                    lines.append("")
            if point.get("editorial_note"):
                lines.extend([f"> **整理说明**：{point['editorial_note']}", ""])
            if point.get("review_tip"):
                lines.extend([f"**复习提示**：{point['review_tip']}", ""])
            for figure in point.get("figures", []):
                image_path = Path(figure["path"]).resolve()
                lines.extend([f"![{figure['caption']}]({image_path.as_posix()})", "", f"*{figure['caption']}*", ""])
            source_links = point.get("source_links") or [{"label": point["source_label"], "url": point["source_url"]}]
            for link in source_links:
                lines.append(f"[▶ 回看来源 · {link['label']}]({link['url']})")
            lines.append("")
        lines.append("")
        linked_ids = {figure.get("image_id") for point in section.get("knowledge_points", []) for figure in point.get("figures", [])}
        for figure in section.get("figures", []):
            if figure.get("image_id") in linked_ids:
                continue
            image_path = Path(figure["path"]).resolve()
            lines.extend([f"![{figure['caption']}]({image_path.as_posix()})", "", f"*{figure['caption']}*", ""])
    review = document.get("review") or {}
    if review.get("knowledge_thread") or review.get("checklist") or review.get("open_questions"):
        lines.extend(["## 课程复习", ""])
        if review.get("knowledge_thread"):
            lines.extend(["### 知识主线", "", review["knowledge_thread"], ""])
        if review.get("checklist"):
            lines.extend(["### 关键规则清单", ""])
            lines.extend(f"- {item}" for item in review["checklist"])
            lines.append("")
        if review.get("open_questions"):
            lines.extend(["### 待回看与未展开问题", ""])
            lines.extend(f"- {item}" for item in review["open_questions"])
            lines.append("")
    if include_transcript and document.get("transcript"):
        lines.extend(["## 完整转写", ""])
        for segment in merge_transcript_segments(document.get("transcript", [])):
            video_id = quote(str(document["metadata"]["video_id"]), safe="")
            url = f"video-study://play/{video_id}?t={int(segment['start_seconds'])}"
            label = f"{hhmmss(segment['start_seconds'])}–{hhmmss(segment['end_seconds'])}"
            lines.append(f"- [{label}]({url}) {segment['text']}")
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def render_docx(document_json: Path, output: Path, project_root: Path) -> None:
    node = find_tool("node")
    renderer = project_root / "scripts" / "render_docx.mjs"
    modules = project_root / "node_modules" / "docx"
    if not renderer.is_file():
        renderer = bundled_path("scripts", "render_docx.mjs")
    if not modules.exists():
        modules = bundled_path("node_modules", "docx")
    if not node:
        raise RuntimeError("未找到 Node.js，无法生成 DOCX")
    if not renderer.is_file():
        raise RuntimeError(f"未找到 Word 渲染脚本：{renderer}")
    if not modules.exists():
        raise RuntimeError("未安装 docx-js，请先运行 npm install")
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([node, str(renderer), str(document_json), str(output)], check=True)


def _font_path() -> Path | None:
    for candidate in (
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
        Path("C:/Windows/Fonts/simsun.ttc"),
    ):
        if candidate.exists():
            return candidate
    return None


def render_pdf_fallback(document: dict, output: Path) -> None:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    font = _font_path()
    font_name = "Helvetica"
    if font:
        try:
            pdfmetrics.registerFont(TTFont("Chinese", str(font), subfontIndex=0))
            font_name = "Chinese"
        except Exception:
            pass
    styles = getSampleStyleSheet()
    navy = colors.HexColor("#17324D")
    teal = colors.HexColor("#167D87")
    ink = colors.HexColor("#243342")
    muted = colors.HexColor("#667786")
    pale = colors.HexColor("#EDF5F5")
    line = colors.HexColor("#D6E2E5")
    title = ParagraphStyle("ChineseTitle", parent=styles["Title"], fontName=font_name, fontSize=24, leading=31, alignment=TA_LEFT, textColor=navy, spaceAfter=4*mm)
    kicker = ParagraphStyle("Kicker", parent=styles["BodyText"], fontName=font_name, fontSize=8.5, leading=11, textColor=teal, spaceAfter=2*mm)
    heading = ParagraphStyle("ChineseHeading", parent=styles["Heading1"], fontName=font_name, fontSize=16, leading=21, textColor=navy, spaceBefore=9*mm, spaceAfter=3*mm, keepWithNext=True)
    point_title = ParagraphStyle("PointTitle", parent=styles["Heading2"], fontName=font_name, fontSize=11.5, leading=17, textColor=navy, spaceAfter=2*mm, keepWithNext=True)
    body = ParagraphStyle("ChineseBody", parent=styles["BodyText"], fontName=font_name, fontSize=10.5, leading=17, textColor=ink, spaceAfter=2.5*mm)
    small = ParagraphStyle("ChineseSmall", parent=body, fontSize=8.5, leading=12, textColor=muted, spaceAfter=1*mm)
    link = ParagraphStyle("SourceLink", parent=small, textColor=teal, spaceBefore=1*mm)

    mode_label = "多视频智能聚合" if document["mode"] == "cloud_aggregate" else "Qwen 智能整理" if document["mode"] == "cloud_summary" else "离线提取"
    document_title = document["metadata"].get("document_title") or document["metadata"]["title"]
    story = [
        Paragraph("VIDEO STUDY NOTES  /  视频资料", kicker),
        Paragraph(html.escape(document_title), title),
        Table([[Paragraph(f"视频时长<br/><b>{html.escape(document['metadata']['duration_label'])}</b>", small),
                Paragraph(f"整理方式<br/><b>{mode_label}</b>", small)]], colWidths=[48*mm, 72*mm], style=TableStyle([
                    ("BACKGROUND", (0, 0), (-1, -1), pale), ("BOX", (0, 0), (-1, -1), 0.5, line),
                    ("INNERGRID", (0, 0), (-1, -1), 0.5, line), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ])),
        Spacer(1, 8*mm), Paragraph("内容导览", heading),
        Table([[Paragraph(html.escape(document["overview"]), body)]], colWidths=[165*mm], style=TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F5F8F9")),
            ("LINEBEFORE", (0, 0), (0, -1), 3, teal), ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10), ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ])), Spacer(1, 2*mm), Paragraph(html.escape(document["notice"]), small),
    ]
    if document.get("learning_objectives"):
        story.append(Paragraph("学习目标", heading))
        story.extend(Paragraph(f"• {html.escape(item)}", body) for item in document["learning_objectives"])
    figures = document.get("figures", [])
    if not document.get("sections") and figures:
        story.append(Paragraph("关键画面", heading))
        for figure in figures:
            image = Image(figure["path"])
            image._restrictSize(165*mm, 90*mm)
            story.append(KeepTogether([image, Paragraph(html.escape(figure["caption"]), small)]))
            story.append(Spacer(1, 3*mm))
    for index, section in enumerate(document.get("sections", []), start=1):
        story.append(Paragraph(f"{index:02d}  /  {html.escape(section['title'])}", heading))
        story.append(Paragraph(html.escape(section.get("summary", "")), body))
        for point_index, point in enumerate(section.get("knowledge_points", []), start=1):
            explanation = html.escape(point.get("explanation", ""))
            block = [Paragraph(f"{index}.{point_index}  {html.escape(point['statement'])}", point_title)]
            if explanation:
                block.append(Paragraph(explanation, body))
            for field, label in _POINT_LIST_FIELDS:
                values = point.get(field) or []
                if values:
                    block.append(Paragraph(f"<b>{html.escape(label)}</b>", body))
                    block.extend(Paragraph(f"• {html.escape(item)}", body) for item in values)
            if point.get("editorial_note"):
                block.append(Paragraph(f"<b>整理说明：</b>{html.escape(point['editorial_note'])}", body))
            if point.get("review_tip"):
                block.append(Paragraph(f"<b>复习提示：</b>{html.escape(point['review_tip'])}", body))
            for figure in point.get("figures", []):
                image = Image(figure["path"])
                image._restrictSize(165*mm, 90*mm)
                block.extend([image, Spacer(1, 1.5*mm), Paragraph(html.escape(figure["caption"]), small)])
            source_links = point.get("source_links") or [{"label": point["source_label"], "url": point["source_url"]}]
            for source in source_links:
                source_url = html.escape(source["url"], quote=True)
                block.append(Paragraph(f"<link href='{source_url}'>▶ 回看来源 · {html.escape(source['label'])}</link>", link))
            story.extend([*block, Spacer(1, 3*mm)])
        linked_ids = {figure.get("image_id") for point in section.get("knowledge_points", []) for figure in point.get("figures", [])}
        for figure in section.get("figures", []):
            if figure.get("image_id") in linked_ids:
                continue
            image = Image(figure["path"])
            image._restrictSize(165*mm, 90*mm)
            story.append(KeepTogether([image, Spacer(1, 1.5*mm), Paragraph(html.escape(figure["caption"]), small)]))
    review = document.get("review") or {}
    if review.get("knowledge_thread") or review.get("checklist") or review.get("open_questions"):
        story.append(Paragraph("课程复习", heading))
        if review.get("knowledge_thread"):
            story.extend([Paragraph("知识主线", point_title), Paragraph(html.escape(review["knowledge_thread"]), body)])
        if review.get("checklist"):
            story.append(Paragraph("关键规则清单", point_title))
            story.extend(Paragraph(f"• {html.escape(item)}", body) for item in review["checklist"])
        if review.get("open_questions"):
            story.append(Paragraph("待回看与未展开问题", point_title))
            story.extend(Paragraph(f"• {html.escape(item)}", body) for item in review["open_questions"])
    if document.get("render_options", {}).get("include_full_transcript", True) and document.get("transcript"):
        story.extend([Spacer(1, 5*mm), Paragraph("完整转写", heading)])
        for segment in merge_transcript_segments(document["transcript"]):
            label = f"{hhmmss(segment['start_seconds'])}–{hhmmss(segment['end_seconds'])}"
            url = html.escape(
                f"video-study://play/{quote(str(document['metadata']['video_id']), safe='')}?t={int(segment['start_seconds'])}",
                quote=True,
            )
            text = f"<link href='{url}' color='#356A8A'><b>{label}</b></link>　{html.escape(segment['text'])}"
            story.append(Paragraph(text, body))
    output.parent.mkdir(parents=True, exist_ok=True)
    pdf = SimpleDocTemplate(str(output), pagesize=A4, rightMargin=22*mm, leftMargin=22*mm, topMargin=18*mm, bottomMargin=18*mm, title=document_title)

    def page_furniture(canvas, doc) -> None:
        canvas.saveState()
        canvas.setStrokeColor(line)
        canvas.setLineWidth(0.5)
        canvas.line(22*mm, 12*mm, 188*mm, 12*mm)
        canvas.setFillColor(muted)
        canvas.setFont(font_name, 8)
        canvas.drawString(22*mm, 8*mm, "VIDEO STUDY NOTES")
        canvas.drawRightString(188*mm, 8*mm, f"{doc.page}")
        canvas.restoreState()

    pdf.build(story, onFirstPage=page_furniture, onLaterPages=page_furniture)


def convert_docx_to_pdf(docx: Path, pdf: Path, document: dict) -> str:
    soffice = find_tool("soffice") or find_tool("libreoffice")
    if soffice:
        subprocess.run([soffice, "--headless", "--convert-to", "pdf", "--outdir", str(pdf.parent), str(docx)], check=True)
        generated = pdf.parent / f"{docx.stem}.pdf"
        if generated != pdf and generated.exists():
            generated.replace(pdf)
        return "libreoffice_from_docx"
    render_pdf_fallback(document, pdf)
    return "reportlab_fallback"
