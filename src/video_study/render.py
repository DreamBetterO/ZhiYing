from __future__ import annotations

import html
import json
import os
import subprocess
from pathlib import Path
from urllib.parse import quote

from .transcript import merge_transcript_segments
from .runtime import find_tool, project_path
from .utils import ensure_not_cancelled, hhmmss, run_cancellable

_POINT_LIST_FIELDS = (
    ("details", "补充细节"), ("steps", "步骤"), ("examples", "课程案例"),
    ("conditions", "适用条件与边界"), ("pitfalls", "易错点"),
)

_BLOCK_LABELS = {
    "rule_list": "规则",
    "steps": "步骤",
    "example": "案例",
    "pitfall": "易错点",
}


def _content_block_items(block: dict) -> list[str]:
    items = [str(item).strip() for item in (block.get("items") or []) if str(item).strip()]
    if not items and str(block.get("text", "") or "").strip():
        items = [str(block["text"]).strip()]
    return items


def _render_visual_group_md(block: dict, lines: list[str], figure_map: dict[str, dict]) -> bool:
    binding_ids = [str(value) for value in block.get("binding_ids", []) if str(value)]
    if not binding_ids:
        binding_ids = [str(block.get("binding_id", ""))]
    figures = [figure_map[binding_id] for binding_id in binding_ids if binding_id in figure_map]
    if not figures:
        return False
    figure = figures[0]
    lead_in = str(block.get("lead_in") or figure.get("reader_focus") or figure.get("why_useful") or "")
    caption = str(block.get("caption") or figure.get("caption") or "")
    takeaway = str(
        block.get("takeaway")
        or figure.get("explanation_for_reader")
        or figure.get("visual_summary")
        or ""
    )
    if lead_in:
        lines.extend([f"**看图重点**：{lead_in.replace('看图重点：', '')}", ""])
    for row in figures:
        row_caption = caption if len(figures) == 1 else str(row.get("caption") or "")
        image_path = Path(row["path"]).resolve()
        lines.extend([f"![{row_caption}]({image_path.as_posix()})", ""])
        if row_caption:
            lines.extend([f"*{row_caption}*", ""])
        if row.get("source_url"):
            label = row.get("timestamp_label") or hhmmss(float(row.get("timestamp_seconds", 0.0)))
            lines.extend([f"[▶ 查看图片来源 · {label}]({row['source_url']})", ""])
    if takeaway:
        label = "这组图帮助理解" if len(figures) > 1 else "这张图帮助理解"
        lines.extend([f"> **{label}**：{takeaway}", ""])
    return True


def _render_content_blocks_md(point: dict, lines: list[str], figure_map: dict[str, dict]) -> bool:
    """按 content_blocks 渲染知识点正文。返回 True 表示已渲染，False 回退旧路径。"""
    blocks = point.get("content_blocks") or []
    if not blocks:
        return False
    has_explicit_caption = any(block.get("type") == "figure_caption" for block in blocks)
    for block in blocks:
        btype = block.get("type", "")
        if btype == "visual_group":
            _render_visual_group_md(block, lines, figure_map)
        elif btype == "paragraph":
            text = block.get("text", "")
            if text:
                lines.extend([text, ""])
        elif btype == "visual_lead_in":
            text = block.get("text", "")
            if text:
                lines.extend([f"**看图重点**：{text.replace('看图重点：', '')}", ""])
        elif btype in ("rule_list", "steps", "example", "pitfall"):
            label = _BLOCK_LABELS.get(btype, btype)
            items = _content_block_items(block)
            if items:
                lines.extend([f"**{label}**", ""])
                lines.extend(f"- {item}" for item in items)
                lines.append("")
        elif btype == "figure":
            binding_id = block.get("binding_id", "")
            figure = figure_map.get(binding_id)
            if figure:
                image_path = Path(figure["path"]).resolve()
                caption = figure.get("caption", "")
                lines.extend([f"![{caption}]({image_path.as_posix()})", ""])
                if not has_explicit_caption:
                    lines.extend([f"*{caption}*", ""])
        elif btype == "figure_caption":
            text = block.get("text", "")
            if text:
                lines.extend([f"*{text}*", ""])
        elif btype == "visual_takeaway":
            text = block.get("text", "")
            if text:
                lines.extend([f"> **这张图帮助理解**：{text}", ""])
        elif btype == "understanding_tip":
            text = block.get("text", "")
            if text:
                lines.extend([f"> **理解提示**：{text}", ""])
        elif btype == "source_links":
            pass  # 来源在末尾统一处理
    return True


def _render_point_md(point: dict, lines: list[str], figure_map: dict[str, dict]) -> None:
    """渲染单个知识点的正文（优先 content_blocks，回退旧字段）。"""
    if _render_content_blocks_md(point, lines, figure_map):
        rendered_ids: set[str] = set()
        for block in point.get("content_blocks", []):
            if block.get("type") not in {"figure", "visual_group"}:
                continue
            rendered_ids.add(str(block.get("binding_id", "")))
            rendered_ids.update(str(value) for value in block.get("binding_ids", []) if str(value))
        for figure in point.get("figures", []):
            if figure.get("binding_id", "") in rendered_ids:
                continue
            focus = figure.get("reader_focus") or figure.get("why_useful") or ""
            takeaway = figure.get("explanation_for_reader") or figure.get("visual_summary") or ""
            if focus:
                lines.extend([f"**看图重点**：{focus.replace('看图重点：', '')}", ""])
            image_path = Path(figure["path"]).resolve()
            lines.extend([f"![{figure['caption']}]({image_path.as_posix()})", "", f"*{figure['caption']}*", ""])
            if takeaway:
                lines.extend([f"> **这张图帮助理解**：{takeaway}", ""])
        return
    # 旧路径
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
    # 旧路径：图片统一在末尾
    for figure in point.get("figures", []):
        image_path = Path(figure["path"]).resolve()
        lines.extend([f"![{figure['caption']}]({image_path.as_posix()})", "", f"*{figure['caption']}*", ""])


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
        lines.extend([f"## {section_index:02d} · {section['title']}", ""])
        if str(section.get("summary", "")).strip():
            lines.extend([f"> {section['summary']}", ""])
        # 构建本 section 的 figure_map（binding_id → figure）
        section_figure_map: dict[str, dict] = {}
        for point in section.get("knowledge_points", []):
            for figure in point.get("figures", []):
                bid = figure.get("binding_id", "")
                if bid:
                    section_figure_map[bid] = figure
        for point_index, point in enumerate(section.get("knowledge_points", []), start=1):
            lines.append(f"### {section_index}.{point_index} {point['statement']}")
            lines.append("")
            _render_point_md(point, lines, section_figure_map)
            # content_blocks 路径中 source_links 块不渲染；这里统一处理来源
            refs = point.get("source_refs", {}) if isinstance(point.get("source_refs"), dict) else {}
            source_links = refs.get("links") or point.get("source_links") or [{
                "label": refs.get("label", point.get("source_label", "")),
                "url": refs.get("url", point.get("source_url", "")),
            }]
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


def render_docx(
    document_json: Path, output: Path, project_root: Path, *, cancel_check=None,
) -> None:
    node = find_tool("node")
    renderer = project_root / "scripts" / "render_docx.mjs"
    modules = project_root / "node_modules" / "docx"
    if not renderer.is_file():
        renderer = project_path("scripts", "render_docx.mjs")
    if not modules.exists():
        modules = project_path("node_modules", "docx")
    if not node:
        raise RuntimeError("未找到 Node.js，无法生成 DOCX")
    if not renderer.is_file():
        raise RuntimeError(f"未找到 Word 渲染脚本：{renderer}")
    if not modules.exists():
        raise RuntimeError("未安装 docx-js，请先运行 npm install")
    output.parent.mkdir(parents=True, exist_ok=True)
    run_cancellable([node, str(renderer), str(document_json), str(output)], cancel_check=cancel_check)


def _font_path() -> Path | None:
    for candidate in (
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
        Path("C:/Windows/Fonts/simsun.ttc"),
    ):
        if candidate.exists():
            return candidate
    return None


def render_pdf_fallback(document: dict, output: Path, *, cancel_check=None) -> None:
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

    def visual_group_flowable(group: dict, figures: list[dict]):
        figure = figures[0]
        lead_in = str(group.get("lead_in") or figure.get("reader_focus") or figure.get("why_useful") or "")
        caption = str(group.get("caption") or figure.get("caption") or "")
        takeaway = str(
            group.get("takeaway")
            or figure.get("explanation_for_reader")
            or figure.get("visual_summary")
            or ""
        )
        visual_rows = []
        source_rows = []
        if lead_in:
            visual_rows.append(Paragraph(
                f"<b>看图重点：</b>{html.escape(lead_in.replace('看图重点：', ''))}",
                body,
            ))
        for row in figures:
            image = Image(row["path"])
            image._restrictSize(158*mm, (82 if len(figures) == 1 else 55)*mm)
            visual_rows.extend([image, Spacer(1, 1.2*mm)])
            row_caption = caption if len(figures) == 1 else str(row.get("caption") or "")
            if row_caption:
                visual_rows.append(Paragraph(html.escape(row_caption), small))
            if row.get("source_url"):
                source_url = html.escape(str(row["source_url"]), quote=True)
                source_label = row.get("timestamp_label") or hhmmss(float(row.get("timestamp_seconds", 0.0)))
                source_rows.append(Paragraph(
                    f"<link href='{source_url}'>▶ 查看图片来源 · {html.escape(str(source_label))}</link>",
                    link,
                ))
        if takeaway:
            label = "这组图帮助理解" if len(figures) > 1 else "这张图帮助理解"
            visual_rows.append(Paragraph(f"<b>{label}：</b>{html.escape(takeaway)}", body))
        visual_rows.extend(source_rows)
        return KeepTogether(visual_rows)

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
        ensure_not_cancelled(cancel_check)
        story.append(Paragraph(f"{index:02d}  /  {html.escape(section['title'])}", heading))
        if str(section.get("summary", "")).strip():
            story.append(Paragraph(html.escape(section['summary']), body))
        for point_index, point in enumerate(section.get("knowledge_points", []), start=1):
            explanation = html.escape(point.get("explanation", ""))
            block = [Paragraph(f"{index}.{point_index}  {html.escape(point['statement'])}", point_title)]
            # 构建 figure_map（binding_id → figure）
            point_figure_map: dict[str, dict] = {}
            for figure in point.get("figures", []):
                bid = figure.get("binding_id", "")
                if bid:
                    point_figure_map[bid] = figure
            # 优先 content_blocks
            content_blocks = point.get("content_blocks") or []
            if content_blocks:
                has_explicit_caption = any(cb.get("type") == "figure_caption" for cb in content_blocks)
                rendered_figure_ids: set[str] = set()
                for cb in content_blocks:
                    cbtype = cb.get("type", "")
                    if cbtype == "visual_group":
                        bids = [str(value) for value in cb.get("binding_ids", []) if str(value)]
                        if not bids:
                            bids = [str(cb.get("binding_id", ""))]
                        group_figures = [point_figure_map[bid] for bid in bids if bid in point_figure_map]
                        if group_figures:
                            block.append(visual_group_flowable(cb, group_figures))
                            rendered_figure_ids.update(bids)
                    elif cbtype == "paragraph":
                        text = cb.get("text", "")
                        if text:
                            block.append(Paragraph(html.escape(text), body))
                    elif cbtype == "visual_lead_in":
                        text = str(cb.get("text", "")).replace("看图重点：", "")
                        if text:
                            block.append(Paragraph(f"<b>看图重点：</b>{html.escape(text)}", body))
                    elif cbtype in ("rule_list", "steps", "example", "pitfall"):
                        label = _BLOCK_LABELS.get(cbtype, cbtype)
                        items = _content_block_items(cb)
                        if items:
                            block.append(Paragraph(f"<b>{html.escape(label)}</b>", body))
                            block.extend(Paragraph(f"• {html.escape(item)}", body) for item in items)
                    elif cbtype == "figure":
                        bid = cb.get("binding_id", "")
                        figure = point_figure_map.get(bid)
                        if figure:
                            image = Image(figure["path"])
                            image._restrictSize(165*mm, 90*mm)
                            block.extend([image, Spacer(1, 1.5*mm)])
                            rendered_figure_ids.add(bid)
                            if not has_explicit_caption:
                                block.append(Paragraph(html.escape(figure.get("caption", "")), small))
                    elif cbtype == "figure_caption":
                        text = cb.get("text", "")
                        if text:
                            block.append(Paragraph(html.escape(text), small))
                    elif cbtype == "visual_takeaway":
                        text = cb.get("text", "")
                        if text:
                            block.append(Paragraph(f"<b>这张图帮助理解：</b>{html.escape(text)}", body))
                    elif cbtype == "understanding_tip":
                        text = cb.get("text", "")
                        if text:
                            block.append(Paragraph(f"<b>理解提示：</b>{html.escape(text)}", body))
                for figure in point.get("figures", []):
                    if figure.get("binding_id", "") in rendered_figure_ids:
                        continue
                    focus = figure.get("reader_focus") or figure.get("why_useful") or ""
                    takeaway = figure.get("explanation_for_reader") or figure.get("visual_summary") or ""
                    if focus:
                        block.append(Paragraph(
                            f"<b>看图重点：</b>{html.escape(str(focus).replace('看图重点：', ''))}",
                            body,
                        ))
                    image = Image(figure["path"])
                    image._restrictSize(165*mm, 90*mm)
                    block.extend([image, Spacer(1, 1.5*mm), Paragraph(html.escape(figure["caption"]), small)])
                    if takeaway:
                        block.append(Paragraph(f"<b>这张图帮助理解：</b>{html.escape(str(takeaway))}", body))
            else:
                # 旧路径
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
            refs = point.get("source_refs", {}) if isinstance(point.get("source_refs"), dict) else {}
            source_links = refs.get("links") or point.get("source_links") or [{
                "label": refs.get("label", point.get("source_label", "")),
                "url": refs.get("url", point.get("source_url", "")),
            }]
            source_flowables = []
            for source in source_links:
                source_url = html.escape(source["url"], quote=True)
                source_flowables.append(Paragraph(
                    f"<link href='{source_url}'>▶ 回看来源 · {html.escape(source['label'])}</link>",
                    link,
                ))
            if source_flowables and len(block) > 1:
                block.append(KeepTogether([block.pop(), *source_flowables]))
            else:
                block.extend(source_flowables)
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

    ensure_not_cancelled(cancel_check)
    pdf.build(story, onFirstPage=page_furniture, onLaterPages=page_furniture)
    ensure_not_cancelled(cancel_check)


def _word_export_to_pdf(docx: Path, pdf: Path, *, cancel_check=None) -> bool:
    """仅执行本机 Microsoft Word 导出；成功返回 True，失败返回 False（不 fallback）。"""
    if os.name != "nt":
        return False
    word_pdf = pdf.with_name(f"{pdf.stem}.word-export{pdf.suffix}")
    quoted_docx = str(docx.resolve()).replace("'", "''")
    quoted_pdf = str(word_pdf.resolve()).replace("'", "''")
    script = (
        "$ErrorActionPreference='Stop';$word=$null;$document=$null;"
        "try{$word=New-Object -ComObject Word.Application;"
        "$word.Visible=$false;$word.DisplayAlerts=0;"
        f"$document=$word.Documents.Open('{quoted_docx}',$false,$true,$false);"
        f"$document.ExportAsFixedFormat('{quoted_pdf}',17)}}"
        "finally{if($null-ne$document){$document.Close($false)};"
        "if($null-ne$word){$word.Quit()};"
        "[GC]::Collect();[GC]::WaitForPendingFinalizers()}"
    )
    try:
        run_cancellable(
            [
                "powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
                "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass", "-Command", script,
            ],
            cancel_check=cancel_check, timeout_seconds=60.0,
        )
        if word_pdf.is_file() and word_pdf.stat().st_size > 0:
            word_pdf.replace(pdf)
            return True
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pass
    finally:
        word_pdf.unlink(missing_ok=True)
    return False


def convert_docx_to_pdf(docx: Path, pdf: Path, document: dict, *, cancel_check=None) -> str:
    """优先使用本机 Microsoft Word 导出；不可用时静默使用内置渲染器。"""
    if _word_export_to_pdf(docx, pdf, cancel_check=cancel_check):
        return "local_word"
    render_pdf_fallback(document, pdf, cancel_check=cancel_check)
    return "built_in"


class DocumentAdapter:
    """封装 Markdown、Node/Word 与内置 PDF 选择的 DocumentPort adapter。

    历史只读适配器：对 schema v3 走 v3_to_v2 投影；V6.1 生产渲染使用
    DocumentAdapterV31（原生消费 Document v3.1）。本类保留用于历史 v2/v3 只读诊断。
    """

    def __init__(self, project_root: Path, *, include_transcript: bool = True) -> None:
        self.project_root = project_root
        self.include_transcript = include_transcript

    def _legacy_render_view(self, document: dict) -> dict:
        if int(document.get("schema_version", 1) or 1) == 3:
            from .document_v3 import v3_to_v2
            legacy = v3_to_v2(document)
        else:
            legacy = dict(document)
        legacy["render_options"] = {
            **dict(legacy.get("render_options", {})),
            "include_full_transcript": self.include_transcript,
        }
        return legacy

    def render_markdown(
        self, document: dict, output: Path, *, source_document: Path | None = None,
    ) -> Path:
        if int(document.get("schema_version", 1) or 1) == 3:
            from .document_v3 import v3_to_v2
            document = v3_to_v2(document)
        render_markdown(document, output, self.include_transcript)
        return output

    def render_word(self, document_json: Path, output: Path, *, cancel_check) -> Path:
        output.parent.mkdir(parents=True, exist_ok=True)
        if not document_json.is_file():
            render_docx(document_json, output, self.project_root, cancel_check=cancel_check)
            return output
        value = json.loads(document_json.read_text(encoding="utf-8"))
        temporary = output.with_suffix(".render-v2.json")
        temporary.write_text(
            json.dumps(self._legacy_render_view(value), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        try:
            render_docx(temporary, output, self.project_root, cancel_check=cancel_check)
        finally:
            temporary.unlink(missing_ok=True)
        return output

    def render_pdf(
        self,
        document: dict,
        word: Path,
        output: Path,
        *,
        source_document: Path | None = None,
        cancel_check,
    ) -> str:
        return convert_docx_to_pdf(
            word, output, self._legacy_render_view(document), cancel_check=cancel_check,
        )


class DocumentAdapterV31:
    """V6.1 生产 DocumentPort：原生消费 Document v3.1（不经过 v3_to_v2）。

    - Markdown：render_v31.render_markdown_v31；
    - Word：scripts/render_docx_v31.mjs（公式生成 Word 原生 OMML）；
    - PDF：优先 Word 转 PDF，不可用时 built-in v3.1 fallback；
    - 不注入旧业务模板；只自动加入合同允许的来源/页码/元数据/无障碍字段。
    """

    def __init__(self, project_root: Path) -> None:
        self.project_root = Path(project_root)

    def _figures_for(self, document_json: Path) -> dict[str, dict]:
        """从文档所在 Workspace 的 visual-evidence.json 解析图片渲染信息。"""
        from .render_v31 import resolve_v31_figures
        # <workspace>/<video_id>/knowledge/document-v3.json → parents[1] = <workspace>/<video_id>
        video_root = document_json.resolve().parents[1]
        document = json.loads(document_json.read_text(encoding="utf-8"))
        return resolve_v31_figures(document, video_root, project_root=self.project_root)

    def render_markdown(
        self, document: dict, output: Path, *, source_document: Path | None = None,
    ) -> Path:
        from .render_v31 import render_markdown_v31
        figures = self._figures_for(source_document) if source_document is not None else {}
        return render_markdown_v31(
            document, output, figure_map=figures, project_root=self.project_root,
        )

    def render_word(self, document_json: Path, output: Path, *, cancel_check) -> Path:
        from .render_v31 import render_docx_v31
        figures = self._figures_for(document_json)
        return render_docx_v31(
            document_json, output, project_root=self.project_root,
            figure_map=figures, cancel_check=cancel_check,
        )

    def render_pdf(
        self,
        document: dict,
        word: Path,
        output: Path,
        *,
        source_document: Path | None = None,
        cancel_check,
    ) -> str:
        from .render_v31 import render_pdf_v31
        figures = self._figures_for(source_document) if source_document is not None else {}
        return render_pdf_v31(
            document, word, output, figure_map=figures, cancel_check=cancel_check,
        )
