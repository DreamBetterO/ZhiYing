// V6.1 native Document v3.1 Word renderer（CP61-2）。
// 直接消费 document-v3.1 组件树；公式生成 Word 原生 OMML；
// 不注入旧业务模板（内容导览/学习目标/课程复习/固定编号/看图重点）。
// 只自动加入合同允许的来源链接、页码、文档元数据与无障碍字段。
// usage: node render_docx_v31.mjs document.json output.docx [projectRoot] [figures.json]
import fs from "node:fs";
import path from "node:path";
import {
  AlignmentType, BorderStyle, Document, ExternalHyperlink, Footer, Header,
  HeadingLevel, ImageRun, LevelFormat, Math as MathBlock, MathFraction, MathRadical,
  MathRun, MathSubScript, MathSuperScript, Packer, PageNumber, Paragraph, TextRun,
} from "docx";

const [inputPath, outputPath, projectRoot, figuresPath] = process.argv.slice(2);
if (!inputPath || !outputPath) throw new Error("usage: render_docx_v31.mjs document.json output.docx [projectRoot] [figures.json]");
const data = JSON.parse(fs.readFileSync(inputPath, "utf8"));
const figures = figuresPath && fs.existsSync(figuresPath) ? JSON.parse(fs.readFileSync(figuresPath, "utf8")) : {};
const root = projectRoot ? path.resolve(projectRoot) : process.cwd();

const NAVY = "17324D";
const TEAL = "167D87";
const INK = "243342";
const MUTED = "667786";
const LINE = "D6E2E5";
const TIP_BORDER = "B0C4DE";
const children = [];
const documentTitle = data.metadata?.document_title || data.metadata?.title || "";

// ---------------------------------------------------------------------------
// LaTeX 子集 -> docx Math（OMML）
// ---------------------------------------------------------------------------
function readBalanced(latex, start) {
  let depth = 0;
  for (let j = start; j < latex.length; j++) {
    if (latex[j] === "{") depth++;
    else if (latex[j] === "}") { depth--; if (depth === 0) return { inner: latex.slice(start + 1, j), next: j + 1 }; }
  }
  return { inner: latex.slice(start + 1), next: latex.length };
}

const MATH_SYMBOLS = {
  alpha: "α", beta: "β", gamma: "γ", delta: "δ", epsilon: "ε", pi: "π",
  theta: "θ", lambda: "λ", mu: "μ", sigma: "σ", phi: "φ", omega: "ω",
  Delta: "Δ", Sigma: "Σ", Pi: "Π", Omega: "Ω",
  int: "∫", sum: "∑", prod: "∏", infty: "∞", partial: "∂", nabla: "∇",
  dots: "…", cdot: "·", times: "×", pm: "±", to: "→", leq: "≤", geq: "≥",
  neq: "≠", approx: "≈", left: "", right: "",
};

function parseLatex(latex) {
  const nodes = [];
  let i = 0;
  let plain = "";
  const flush = () => { if (plain) { nodes.push(new MathRun(plain)); plain = ""; } };
  while (i < latex.length) {
    const ch = latex[i];
    if (ch === "\\") {
      const m = latex.slice(i).match(/^\\([a-zA-Z]+)/);
      if (m) {
        const name = m[1];
        if (name === "frac") {
          flush();
          const a = readBalanced(latex, i + 5);
          const b = readBalanced(latex, a.next);
          nodes.push(new MathFraction({ numerator: parseLatex(a.inner), denominator: parseLatex(b.inner) }));
          i = b.next;
          continue;
        }
        if (name === "sqrt") {
          flush();
          const a = readBalanced(latex, i + 5);
          nodes.push(new MathRadical({ children: parseLatex(a.inner) }));
          i = a.next;
          continue;
        }
        if (name in MATH_SYMBOLS) {
          flush();
          if (MATH_SYMBOLS[name]) nodes.push(new MathRun(MATH_SYMBOLS[name]));
          i += name.length + 1;
          continue;
        }
        flush();
        nodes.push(new MathRun(name));
        i += name.length + 1;
        continue;
      }
      flush();
      nodes.push(new MathRun("\\"));
      i++;
      continue;
    }
    if (ch === "^" || ch === "_") {
      flush();
      const nxt = latex[i + 1];
      const base = nodes.pop() || new MathRun("");
      if (nxt === "{") {
        const a = readBalanced(latex, i + 2);
        const script = parseLatex(a.inner);
        nodes.push(ch === "^"
          ? new MathSuperScript({ children: [base], superScript: script })
          : new MathSubScript({ children: [base], subScript: script }));
        i = a.next;
      } else {
        const script = [new MathRun(nxt)];
        nodes.push(ch === "^"
          ? new MathSuperScript({ children: [base], superScript: script })
          : new MathSubScript({ children: [base], subScript: script }));
        i += 2;
      }
      continue;
    }
    plain += ch;
    i++;
  }
  flush();
  return nodes;
}

function parseInline(text, options = {}) {
  const source = String(text || "");
  const nodes = [];
  let index = 0;
  const pushText = (value) => {
    if (value) nodes.push(new TextRun({ text: value, ...options }));
  };
  while (index < source.length) {
    const boldAt = source.indexOf("**", index);
    const mathAt = source.indexOf("$", index);
    const candidates = [boldAt, mathAt].filter((value) => value >= 0);
    if (!candidates.length) {
      pushText(source.slice(index));
      break;
    }
    const markerAt = Math.min(...candidates);
    pushText(source.slice(index, markerAt));
    if (markerAt === boldAt) {
      const end = source.indexOf("**", markerAt + 2);
      if (end < 0) {
        pushText(source.slice(markerAt));
        break;
      }
      nodes.push(...parseInline(source.slice(markerAt + 2, end), { ...options, bold: true }));
      index = end + 2;
      continue;
    }
    const delimiter = source.startsWith("$$", markerAt) ? "$$" : "$";
    const end = source.indexOf(delimiter, markerAt + delimiter.length);
    if (end < 0) {
      pushText(source.slice(markerAt));
      break;
    }
    const latex = source.slice(markerAt + delimiter.length, end).trim();
    if (latex) nodes.push(new MathBlock({ children: parseLatex(latex) }));
    index = end + delimiter.length;
  }
  return nodes;
}

// ---------------------------------------------------------------------------
// 组件树 -> docx children
// ---------------------------------------------------------------------------
function resolveFigure(component) {
  const visualId = String(component.visual_id || "");
  const entry = figures[visualId];
  if (!entry || !entry.path) return null;
  const file = path.isAbsolute(entry.path) ? entry.path : path.join(root, entry.path);
  if (!fs.existsSync(file)) return null;
  const extension = path.extname(file).slice(1).toLowerCase().replace("jpg", "jpeg");
  return { ...entry, file, extension };
}

function sourceLinks(component) {
  const refs = component.source_refs || {};
  const links = Array.isArray(refs.links) && refs.links.length ? refs.links
    : Array.isArray(component.links) && component.links.length ? component.links
      : refs.url ? [{ label: refs.label || "", url: refs.url }] : [];
  return links.map((source) => new Paragraph({
    style: "SourceLink",
    indent: { left: 520 },
    children: [new ExternalHyperlink({
      link: source.url,
      children: [new TextRun({ text: `▶ 回看来源  ·  ${source.label}`, style: "Hyperlink", bold: true })],
    })],
  }));
}

function renderComponent(component) {
  const type = component.type || "";
  switch (type) {
    case "heading": {
      const level = Number(component.level || 2);
      const heading = level <= 1 ? HeadingLevel.HEADING_1 : level === 2 ? HeadingLevel.HEADING_2 : HeadingLevel.HEADING_3;
      children.push(new Paragraph({ heading, children: [new TextRun({ text: String(component.text || "") })] }));
      return;
    }
    case "paragraph":
      if (component.text) children.push(new Paragraph({ style: "PointBody", children: parseInline(component.text) }));
      return;
    case "list":
      for (const item of component.items || []) {
        children.push(new Paragraph({
          style: "PointBody",
          numbering: { reference: "knowledge-bullets", level: 0 },
          children: parseInline(item),
        }));
      }
      return;
    case "equation": {
      const latex = String(component.latex || "");
      if (!latex) return;
      children.push(new Paragraph({
        style: "Equation",
        alignment: AlignmentType.CENTER,
        children: [new MathBlock({ children: parseLatex(latex) })],
      }));
      return;
    }
    case "image": {
      const figure = resolveFigure(component);
      if (!figure) return;
      const origW = figure.width || 1280;
      const origH = figure.height || 720;
      const maxW = 535;
      const maxH = 350;
      const scale = Math.min(maxW / origW, maxH / origH, 1.0);
      const width = Math.round(origW * scale);
      const height = Math.round(origH * scale);
      const caption = String(component.caption || "");
      children.push(new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing: { before: 220, after: 80 },
        keepNext: true,
        children: [new ImageRun({
          type: figure.extension,
          data: fs.readFileSync(figure.file),
          transformation: { width, height },
          altText: { title: caption, description: String(component.alt_text || caption), name: String(component.visual_id || "") },
        })],
      }));
      if (caption) children.push(new Paragraph({
        style: "Caption", alignment: AlignmentType.CENTER, children: [new TextRun(caption)],
      }));
      children.push(...sourceLinks(component));
      return;
    }
    case "callout":
      if (component.text) children.push(new Paragraph({
        style: "LeadCallout",
        border: { left: { style: BorderStyle.SINGLE, size: 12, color: TIP_BORDER, space: 8 } },
        children: parseInline(component.text),
      }));
      return;
    case "source_reference":
      children.push(...sourceLinks(component));
      return;
    case "page_break":
      children.push(new Paragraph({ children: [new TextRun("")], pageBreakBefore: true }));
      return;
    case "container":
      for (const child of component.children || []) renderComponent(child);
      return;
    case "table":
      // 本地 v3.1 表格组件：以列表形式兜底（确定性 fallback，contract component_fallbacks.table=paragraph）
      for (const row of component.rows || []) {
        children.push(new Paragraph({
          style: "PointBody",
          children: [new TextRun(String(Array.isArray(row) ? row.join("  |  ") : row))],
        }));
      }
      return;
    default:
      return;
  }
}

// ---------------------------------------------------------------------------
// 文档外壳（仅合同允许的元数据/页码/来源）
// ---------------------------------------------------------------------------
children.push(new Paragraph({ style: "Kicker", spacing: { before: 500, after: 100 }, children: [new TextRun("VIDEO STUDY NOTES  /  视频资料")] }));
children.push(new Paragraph({ spacing: { after: 180 }, children: [new TextRun({ text: documentTitle, bold: true, size: 48, color: NAVY })] }));
if (data.metadata?.duration_label) {
  children.push(new Paragraph({
    style: "Metadata",
    shading: { fill: "EDF5F5" },
    border: { top: { style: BorderStyle.SINGLE, size: 4, color: LINE, space: 8 }, bottom: { style: BorderStyle.SINGLE, size: 4, color: LINE, space: 8 } },
    children: [new TextRun({ text: `视频时长  ${data.metadata.duration_label}`, bold: true })],
  }));
}
for (const component of data.components || []) renderComponent(component);

const doc = new Document({
  creator: "ZhiYing-v31",
  title: documentTitle,
  styles: {
    default: { document: { run: { font: "Microsoft YaHei", size: 21, color: INK }, paragraph: { spacing: { after: 130, line: 340 } } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { font: "Microsoft YaHei", bold: true, size: 31, color: NAVY },
        paragraph: { spacing: { before: 380, after: 150 }, outlineLevel: 0, keepNext: true,
          border: { bottom: { style: BorderStyle.SINGLE, size: 3, color: LINE, space: 5 } } } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { font: "Microsoft YaHei", bold: true, size: 25, color: NAVY },
        paragraph: { spacing: { before: 260, after: 110 }, outlineLevel: 1, keepNext: true } },
      { id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { font: "Microsoft YaHei", bold: true, size: 22, color: TEAL },
        paragraph: { spacing: { before: 200, after: 90 }, outlineLevel: 2, keepNext: true } },
      { id: "Kicker", name: "Kicker", basedOn: "Normal", run: { font: "Microsoft YaHei", bold: true, size: 18, color: TEAL }, paragraph: { spacing: { after: 100 } } },
      { id: "Metadata", name: "Metadata", basedOn: "Normal", run: { font: "Microsoft YaHei", size: 19, color: MUTED }, paragraph: { spacing: { before: 80, after: 220 }, indent: { left: 160, right: 160 } } },
      { id: "LeadCallout", name: "Lead Callout", basedOn: "Normal", run: { font: "Microsoft YaHei", size: 22, color: INK }, paragraph: { spacing: { before: 60, after: 160, line: 360 }, indent: { left: 220, right: 220 } } },
      { id: "PointBody", name: "Point Body", basedOn: "Normal", run: { font: "Microsoft YaHei", size: 21, color: INK }, paragraph: { spacing: { after: 80, line: 350 }, indent: { left: 520 } } },
      { id: "Equation", name: "Equation", basedOn: "Normal", run: { font: "Cambria Math", size: 24, color: INK }, paragraph: { spacing: { before: 100, after: 140 } } },
      { id: "SourceLink", name: "Source Link", basedOn: "Normal", run: { font: "Microsoft YaHei", size: 18, color: TEAL }, paragraph: { spacing: { after: 170 } } },
      { id: "Caption", name: "Caption", basedOn: "Normal", run: { font: "Microsoft YaHei", size: 17, color: MUTED, italics: true }, paragraph: { spacing: { after: 180 } } },
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
