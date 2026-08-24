import fs from "node:fs";
import path from "node:path";
import {
  AlignmentType, BorderStyle, Document, ExternalHyperlink, Footer, Header,
  HeadingLevel, ImageRun, LevelFormat, Packer, PageNumber, Paragraph, TextRun,
} from "docx";

const [inputPath, outputPath] = process.argv.slice(2);
if (!inputPath || !outputPath) throw new Error("usage: render_docx.mjs document.json output.docx");
const data = JSON.parse(fs.readFileSync(inputPath, "utf8"));
const children = [];
const NAVY = "17324D";
const TEAL = "167D87";
const INK = "243342";
const MUTED = "667786";
const PALE = "EDF5F5";
const LINE = "D6E2E5";
const TIP_BORDER = "B0C4DE";
const documentTitle = data.metadata.document_title || data.metadata.title;
const BLOCK_LABELS = { rule_list: "规则", steps: "步骤", example: "案例", pitfall: "易错点" };

function joinedText(rows) {
  let result = "";
  for (const row of rows) {
    const text = String(row.text || "").trim();
    if (!text) continue;
    if (result && !/[，。！？；：,.!?;:]$/.test(result) && !/^[，。！？；：,.!?;:]/.test(text)) result += "，";
    result += text;
  }
  return result;
}

function mergeTranscript(rows, maxChars = 120, maxSeconds = 30) {
  const merged = [];
  let current = [];
  const flush = () => {
    if (!current.length) return;
    merged.push({ text: joinedText(current), start_seconds: current[0].start_seconds, end_seconds: current.at(-1).end_seconds });
  };
  for (const row of rows) {
    if (!String(row.text || "").trim()) continue;
    const proposed = [...current, row];
    const gap = current.length ? row.start_seconds - current.at(-1).end_seconds : 0;
    const duration = current.length ? row.end_seconds - current[0].start_seconds : 0;
    if (current.length && (gap > 1.5 || duration > maxSeconds || joinedText(proposed).length > maxChars)) {
      flush(); current = [];
    }
    current.push(row);
  }
  flush();
  return merged;
}

function timeLabel(seconds) {
  return new Date(seconds * 1000).toISOString().slice(11, 19);
}

function sourceLinks(point, indent = 520) {
  const refs = point.source_refs || {};
  const links = refs.links?.length
    ? refs.links
    : point.source_links?.length
      ? point.source_links
      : [{ label: refs.label || point.source_label || "", url: refs.url || point.source_url || "" }];
  return links.map((source) => new Paragraph({
    style: "SourceLink",
    indent: { left: indent },
    children: [new ExternalHyperlink({
      link: source.url,
      children: [new TextRun({ text: `▶ 回看来源  ·  ${source.label}`, style: "Hyperlink", bold: true })],
    })],
  }));
}

function addFigure(figure) {
  if (!fs.existsSync(figure.path)) return;
  const extension = path.extname(figure.path).slice(1).toLowerCase().replace("jpg", "jpeg");
  const data = fs.readFileSync(figure.path);
  // 读取真实宽高，等比缩放
  const origW = figure.width || 1280;
  const origH = figure.height || 720;
  const maxW = 535;
  const maxH = 350;
  const scale = Math.min(maxW / origW, maxH / origH, 1.0);
  const w = Math.round(origW * scale);
  const h = Math.round(origH * scale);
  const caption = figure.reader_focus
    ? `观察重点：${figure.reader_focus}\n来源：${figure.timestamp_label || timeLabel(figure.timestamp_seconds)}`
    : figure.caption;
  children.push(new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 220, after: 80 },
    keepNext: true,
    children: [new ImageRun({
      type: extension,
      data,
      transformation: { width: w, height: h },
      altText: { title: caption, description: caption, name: figure.image_id },
    })],
  }));
  children.push(new Paragraph({ style: "Caption", alignment: AlignmentType.CENTER, children: [new TextRun(caption)] }));
}

function addVisualGroup(group, inputFigures) {
  const figures = (Array.isArray(inputFigures) ? inputFigures : [inputFigures]).filter((figure) => figure && fs.existsSync(figure.path));
  if (!figures.length) return false;
  const first = figures[0];
  const leadIn = String(group.lead_in || first.reader_focus || first.why_useful || "").replace(/^看图重点[:：]\s*/, "");
  const takeaway = String(group.takeaway || first.explanation_for_reader || first.visual_summary || "");

  if (leadIn) children.push(new Paragraph({
    style: "PointBody",
    keepNext: true,
    keepLines: true,
    spacing: { before: 80, after: 50 },
    children: [new TextRun({ text: "看图重点：", bold: true, color: TEAL }), new TextRun(leadIn)],
  }));
  for (const figure of figures) {
    const extension = path.extname(figure.path).slice(1).toLowerCase().replace("jpg", "jpeg");
    const imageData = fs.readFileSync(figure.path);
    const origW = figure.width || 1280;
    const origH = figure.height || 720;
    const maxHeight = figures.length === 1 ? 315 : 205;
    const scale = Math.min(515 / origW, maxHeight / origH, 1.0);
    const width = Math.round(origW * scale);
    const height = Math.round(origH * scale);
    const caption = String(figures.length === 1 ? (group.caption || figure.caption || "") : (figure.caption || ""));
    children.push(new Paragraph({
      alignment: AlignmentType.CENTER,
      keepNext: true,
      keepLines: true,
      spacing: { before: 60, after: 45 },
      children: [new ImageRun({
        type: extension,
        data: imageData,
        transformation: { width, height },
        altText: { title: caption, description: caption, name: figure.image_id },
      })],
    }));
    if (caption) children.push(new Paragraph({
      style: "Caption",
      alignment: AlignmentType.CENTER,
      keepNext: true,
      keepLines: true,
      spacing: { after: 70 },
      children: [new TextRun(caption)],
    }));
  }
  if (takeaway) children.push(new Paragraph({
    style: "PointBody",
    keepNext: figures.some((figure) => figure.source_url),
    keepLines: true,
    spacing: { after: 55 },
    children: [new TextRun({ text: figures.length > 1 ? "这组图帮助理解：" : "这张图帮助理解：", bold: true, color: TEAL }), new TextRun(takeaway)],
  }));
  for (const [index, figure] of figures.entries()) {
    if (!figure.source_url) continue;
    const sourceLabel = figure.timestamp_label || timeLabel(figure.timestamp_seconds || 0);
    children.push(new Paragraph({
      style: "SourceLink",
      keepNext: index < figures.length - 1,
      keepLines: true,
      indent: { left: 520 },
      spacing: { after: 100 },
      children: [new ExternalHyperlink({
        link: figure.source_url,
        children: [new TextRun({ text: `▶ 查看图片来源  ·  ${sourceLabel}`, style: "Hyperlink", bold: true })],
      })],
    }));
  }
  return true;
}

function renderContentBlocks(point) {
  const blocks = point.content_blocks || [];
  if (!blocks.length) return false;
  const hasExplicitCaption = blocks.some((block) => block.type === "figure_caption");
  const renderedFigureIds = new Set();
  const figureMap = {};
  for (const figure of (point.figures || [])) {
    if (figure.binding_id) figureMap[figure.binding_id] = figure;
  }
  for (const block of blocks) {
    const btype = block.type || "";
    if (btype === "visual_group") {
      const bindingIds = block.binding_ids?.length ? block.binding_ids : [block.binding_id];
      const figures = bindingIds.map((bindingId) => figureMap[bindingId]).filter(Boolean);
      if (addVisualGroup(block, figures)) for (const bindingId of bindingIds) renderedFigureIds.add(bindingId);
    } else if (btype === "paragraph") {
      if (block.text) children.push(new Paragraph({ style: "PointBody", children: [new TextRun(block.text)] }));
    } else if (btype === "visual_lead_in") {
      const text = String(block.text || "").replace(/^看图重点[:：]\s*/, "");
      if (text) children.push(new Paragraph({
        style: "PointBody",
        keepNext: true,
        children: [new TextRun({ text: "看图重点：", bold: true, color: TEAL }), new TextRun(text)],
      }));
    } else if (btype in BLOCK_LABELS) {
      const items = block.items?.length ? block.items : (block.text ? [String(block.text)] : []);
      addLabeledList(BLOCK_LABELS[btype], items);
    } else if (btype === "figure") {
      const fig = figureMap[block.binding_id];
      if (fig) {
        addFigure(fig);
        renderedFigureIds.add(block.binding_id);
        if (hasExplicitCaption) children.pop();
      }
    } else if (btype === "figure_caption") {
      if (block.text) children.push(new Paragraph({ style: "Caption", alignment: AlignmentType.CENTER, children: [new TextRun(String(block.text))] }));
    } else if (btype === "visual_takeaway") {
      if (block.text) children.push(new Paragraph({
        style: "PointBody",
        children: [new TextRun({ text: "这张图帮助理解：", bold: true, color: TEAL }), new TextRun(String(block.text))],
      }));
    } else if (btype === "understanding_tip") {
      if (block.text) children.push(new Paragraph({
        style: "LeadCallout",
        border: { left: { style: BorderStyle.SINGLE, size: 12, color: TIP_BORDER, space: 8 } },
        children: [new TextRun({ text: "理解提示：", bold: true }), new TextRun(block.text)],
      }));
    }
  }
  for (const figure of point.figures || []) {
    if (figure.binding_id && renderedFigureIds.has(figure.binding_id)) continue;
    const focus = figure.reader_focus || figure.why_useful || "";
    const takeaway = figure.explanation_for_reader || figure.visual_summary || "";
    if (focus) children.push(new Paragraph({
      style: "PointBody",
      keepNext: true,
      children: [new TextRun({ text: "看图重点：", bold: true, color: TEAL }), new TextRun(String(focus).replace(/^看图重点[:：]\s*/, ""))],
    }));
    addFigure(figure);
    if (takeaway) children.push(new Paragraph({
      style: "PointBody",
      children: [new TextRun({ text: "这张图帮助理解：", bold: true, color: TEAL }), new TextRun(String(takeaway))],
    }));
  }
  return true;
}

function addLabeledList(label, values) {
  if (!values?.length) return;
  children.push(new Paragraph({ style: "PointBody", keepNext: true, children: [new TextRun({ text: label, bold: true, color: NAVY })] }));
  for (const value of values) {
    children.push(new Paragraph({ style: "PointBody", numbering: { reference: "knowledge-bullets", level: 0 }, children: [new TextRun(String(value))] }));
  }
}

children.push(new Paragraph({
  style: "Kicker",
  spacing: { before: 500, after: 100 },
  children: [new TextRun("VIDEO STUDY NOTES  /  视频资料")],
}));
children.push(new Paragraph({
  spacing: { after: 180 },
  children: [new TextRun({ text: documentTitle, bold: true, size: 48, color: NAVY })],
}));
children.push(new Paragraph({
  style: "Metadata",
  shading: { fill: PALE },
  border: {
    top: { style: BorderStyle.SINGLE, size: 4, color: LINE, space: 8 },
    bottom: { style: BorderStyle.SINGLE, size: 4, color: LINE, space: 8 },
  },
  children: [new TextRun({ text: `视频时长  ${data.metadata.duration_label}`, bold: true }), new TextRun("     |     "),
    new TextRun({ text: `整理方式  ${data.mode === "cloud_aggregate" ? "多视频智能聚合" : data.mode === "cloud_summary" ? "Qwen 智能整理" : "离线提取"}`, bold: true })],
}));
children.push(new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun("内容导览")] }));
children.push(new Paragraph({
  style: "LeadCallout",
  shading: { fill: "F5F8F9" },
  border: { left: { style: BorderStyle.SINGLE, size: 18, color: TEAL, space: 10 } },
  children: [new TextRun(data.overview)],
}));
children.push(new Paragraph({ style: "Notice", children: [new TextRun(data.notice)] }));

if (data.learning_objectives?.length) {
  children.push(new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun("学习目标")] }));
  for (const item of data.learning_objectives) {
    children.push(new Paragraph({ style: "PointBody", numbering: { reference: "knowledge-bullets", level: 0 }, children: [new TextRun(String(item))] }));
  }
}

if (!data.sections.length && data.figures.length) {
  children.push(new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun("关键画面")] }));
  for (const figure of data.figures) addFigure(figure);
}

for (const [sectionIndex, section] of data.sections.entries()) {
  children.push(new Paragraph({
    heading: HeadingLevel.HEADING_1,
    children: [new TextRun({ text: `${String(sectionIndex + 1).padStart(2, "0")}  /  `, color: TEAL }), new TextRun(section.title)],
  }));
  if (section.summary) children.push(new Paragraph({ style: "SectionSummary", children: [new TextRun(section.summary)] }));
  for (const [pointIndex, point] of section.knowledge_points.entries()) {
    children.push(new Paragraph({
      style: "PointTitle",
      border: { left: { style: BorderStyle.SINGLE, size: 12, color: TEAL, space: 8 } },
      keepNext: true,
      children: [new TextRun({ text: `${sectionIndex + 1}.${pointIndex + 1}  `, color: TEAL, bold: true }), new TextRun({ text: point.statement, bold: true })],
    }));
    const hasBlocks = renderContentBlocks(point);
    if (!hasBlocks) {
      if (point.explanation) children.push(new Paragraph({ style: "PointBody", children: [new TextRun(point.explanation)] }));
      addLabeledList("补充细节", point.details);
      addLabeledList("步骤", point.steps);
      addLabeledList("课程案例", point.examples);
      addLabeledList("适用条件与边界", point.conditions);
      addLabeledList("易错点", point.pitfalls);
      if (point.editorial_note) children.push(new Paragraph({ style: "LeadCallout", children: [new TextRun({ text: "整理说明：", bold: true }), new TextRun(point.editorial_note)] }));
      if (point.review_tip) children.push(new Paragraph({ style: "PointBody", children: [new TextRun({ text: "复习提示：", bold: true, color: TEAL }), new TextRun(point.review_tip)] }));
      for (const figure of point.figures || []) addFigure(figure);
    }
    children.push(...sourceLinks(point));
  }
  const linkedFigureIds = new Set(section.knowledge_points.flatMap((point) => (point.figures || []).map((figure) => figure.image_id)));
  for (const figure of section.figures || []) if (!linkedFigureIds.has(figure.image_id)) addFigure(figure);
}

const review = data.review || {};
if (review.knowledge_thread || review.checklist?.length || review.open_questions?.length) {
  children.push(new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun("课程复习")] }));
  if (review.knowledge_thread) {
    children.push(new Paragraph({ style: "PointTitle", children: [new TextRun({ text: "知识主线", bold: true })] }));
    children.push(new Paragraph({ style: "PointBody", children: [new TextRun(review.knowledge_thread)] }));
  }
  addLabeledList("关键规则清单", review.checklist);
  addLabeledList("待回看与未展开问题", review.open_questions);
}

if ((data.render_options?.include_full_transcript ?? false) && data.transcript.length) {
  children.push(new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun("完整转写")], pageBreakBefore: true }));
  children.push(new Paragraph({ style: "Notice", children: [new TextRun("逐字稿用于检索与核对，正文知识点以章节资料为准。")]}));
  for (const segment of mergeTranscript(data.transcript)) {
    children.push(new Paragraph({ style: "Transcript", children: [
      new ExternalHyperlink({
        link: `video-study://play/${encodeURIComponent(data.metadata.video_id)}?t=${Math.floor(segment.start_seconds)}`,
        children: [new TextRun({ text: `${timeLabel(segment.start_seconds)}-${timeLabel(segment.end_seconds)}  `, bold: true, style: "Hyperlink" })],
      }),
      new TextRun(segment.text),
    ] }));
  }
}

const doc = new Document({
  creator: "ZhiYing",
  title: documentTitle,
  description: data.notice,
  styles: {
    default: { document: { run: { font: "Microsoft YaHei", size: 21, color: INK }, paragraph: { spacing: { after: 130, line: 340 } } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { font: "Microsoft YaHei", bold: true, size: 31, color: NAVY },
        paragraph: { spacing: { before: 380, after: 150 }, outlineLevel: 0, keepNext: true,
          border: { bottom: { style: BorderStyle.SINGLE, size: 3, color: LINE, space: 5 } } } },
      { id: "Kicker", name: "Kicker", basedOn: "Normal", run: { font: "Microsoft YaHei", bold: true, size: 18, color: TEAL }, paragraph: { spacing: { after: 100 } } },
      { id: "Metadata", name: "Metadata", basedOn: "Normal", run: { font: "Microsoft YaHei", size: 19, color: MUTED }, paragraph: { spacing: { before: 80, after: 220 }, indent: { left: 160, right: 160 } } },
      { id: "LeadCallout", name: "Lead Callout", basedOn: "Normal", run: { font: "Microsoft YaHei", size: 22, color: INK }, paragraph: { spacing: { before: 60, after: 160, line: 360 }, indent: { left: 220, right: 220 } } },
      { id: "Notice", name: "Notice", basedOn: "Normal", run: { font: "Microsoft YaHei", size: 17, color: MUTED }, paragraph: { spacing: { before: 60, after: 180 } } },
      { id: "SectionSummary", name: "Section Summary", basedOn: "Normal", run: { font: "Microsoft YaHei", size: 21, color: MUTED, italics: true }, paragraph: { spacing: { after: 220, line: 340 } } },
      { id: "PointTitle", name: "Point Title", basedOn: "Normal", run: { font: "Microsoft YaHei", size: 23, color: NAVY }, paragraph: { spacing: { before: 210, after: 80 }, indent: { left: 300 } } },
      { id: "PointBody", name: "Point Body", basedOn: "Normal", run: { font: "Microsoft YaHei", size: 21, color: INK }, paragraph: { spacing: { after: 80, line: 350 }, indent: { left: 520 } } },
      { id: "SourceLink", name: "Source Link", basedOn: "Normal", run: { font: "Microsoft YaHei", size: 18, color: TEAL }, paragraph: { spacing: { after: 170 } } },
      { id: "Caption", name: "Caption", basedOn: "Normal", run: { font: "Microsoft YaHei", size: 17, color: MUTED, italics: true }, paragraph: { spacing: { after: 180 } } },
      { id: "Transcript", name: "Transcript", basedOn: "Normal", run: { font: "Microsoft YaHei", size: 19, color: INK }, paragraph: { spacing: { after: 90, line: 310 } } },
    ],
  },
  numbering: { config: [{ reference: "knowledge-bullets", levels: [{ level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 520, hanging: 260 } } } }] }] },
  sections: [{
    properties: { page: { size: { width: 11906, height: 16838 }, margin: { top: 1020, right: 1247, bottom: 1020, left: 1247, header: 480, footer: 480 } } },
    headers: { default: new Header({ children: [new Paragraph({
      alignment: AlignmentType.RIGHT,
      border: { bottom: { style: BorderStyle.SINGLE, size: 3, color: LINE, space: 4 } },
      children: [new TextRun({ text: "VIDEO STUDY NOTES", size: 15, color: MUTED })],
    })] }) },
    footers: { default: new Footer({ children: [new Paragraph({
      alignment: AlignmentType.RIGHT,
      border: { top: { style: BorderStyle.SINGLE, size: 3, color: LINE, space: 4 } },
      children: [new TextRun({ text: "视频资料  ·  ", size: 16, color: MUTED }), new TextRun({ children: [PageNumber.CURRENT], size: 16, color: MUTED })],
    })] }) },
    children,
  }],
});

const buffer = await Packer.toBuffer(doc);
fs.mkdirSync(path.dirname(outputPath), { recursive: true });
fs.writeFileSync(outputPath, buffer);
