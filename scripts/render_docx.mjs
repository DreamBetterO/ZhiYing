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
const documentTitle = data.metadata.document_title || data.metadata.title;

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
  const links = point.source_links?.length ? point.source_links : [{ label: point.source_label, url: point.source_url }];
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
  children.push(new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 220, after: 80 },
    keepNext: true,
    children: [new ImageRun({
      type: extension,
      data: fs.readFileSync(figure.path),
      transformation: { width: 535, height: 301 },
      altText: { title: figure.caption, description: figure.caption, name: figure.image_id },
    })],
  }));
  children.push(new Paragraph({ style: "Caption", alignment: AlignmentType.CENTER, children: [new TextRun(figure.caption)] }));
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
    if (point.explanation) children.push(new Paragraph({ style: "PointBody", children: [new TextRun(point.explanation)] }));
    addLabeledList("补充细节", point.details);
    addLabeledList("步骤", point.steps);
    addLabeledList("课程案例", point.examples);
    addLabeledList("适用条件与边界", point.conditions);
    addLabeledList("易错点", point.pitfalls);
    if (point.editorial_note) children.push(new Paragraph({ style: "LeadCallout", children: [new TextRun({ text: "整理说明：", bold: true }), new TextRun(point.editorial_note)] }));
    if (point.review_tip) children.push(new Paragraph({ style: "PointBody", children: [new TextRun({ text: "复习提示：", bold: true, color: TEAL }), new TextRun(point.review_tip)] }));
    for (const figure of point.figures || []) addFigure(figure);
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
  creator: "video-study-demo",
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
