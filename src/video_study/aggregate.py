from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import quote

from .config import AppConfig
from .providers import FallbackChatClient
from .render import convert_docx_to_pdf, render_docx, render_markdown
from .utils import now_iso, safe_name, write_json

_AGGREGATE_GENERATOR_VERSION = 2


def _source_link(document: dict, point: dict, title: str) -> dict:
    video_id = str(document.get("metadata", {}).get("video_id", ""))
    start = int(float(point.get("start_seconds", 0) or 0))
    current_url = str(point.get("source_url", ""))
    url = current_url if current_url.startswith("video-study://play/") else f"video-study://play/{quote(video_id, safe='')}?t={start}"
    return {"label": f"{title} · {point.get('source_label', '')}", "url": url}


def _point_source_text(point: dict) -> str:
    parts = [str(point.get("explanation", ""))]
    for field in ("details", "steps", "examples", "conditions", "pitfalls"):
        parts.extend(str(item) for item in (point.get(field) or []) if str(item).strip())
    if point.get("editorial_note"):
        parts.append(f"整理说明：{point['editorial_note']}")
    return "；".join(item.strip() for item in parts if item.strip())


def _aggregate_source(documents: list[dict]) -> tuple[str, dict[str, dict]]:
    lines: list[str] = []
    points: dict[str, dict] = {}
    counter = 0
    for doc_index, document in enumerate(documents, start=1):
        title = document.get("metadata", {}).get("document_title") or document.get("metadata", {}).get("title", f"视频 {doc_index}")
        lines.extend([f"## 视频 {doc_index}：{title}", str(document.get("overview", ""))])
        for section in document.get("sections", []):
            lines.append(f"### {section.get('title', '')}")
            for point in section.get("knowledge_points", []):
                counter += 1
                point_id = f"point_{counter:04d}"
                links = [_source_link(document, point, title)]
                point_figures = point.get("figures", [])
                if not point_figures:
                    point_figures = [
                        figure for figure in section.get("figures", [])
                        if float(point.get("start_seconds", 0)) <= float(figure.get("timestamp_seconds", 0)) <= float(point.get("end_seconds", 0))
                    ]
                points[point_id] = {"title": title, "point": point, "links": links, "figures": point_figures}
                lines.append(f"- [{point_id}] {point.get('statement', '')}：{_point_source_text(point)}")
        lines.append("")
    return "\n".join(lines).strip(), points


def _validate_aggregate(payload: dict, point_ids: set[str]) -> None:
    if len(str(payload.get("document_title", "")).strip()) < 2:
        raise ValueError("聚合结果缺少标题")
    if len(str(payload.get("overview", "")).strip()) < 20:
        raise ValueError("聚合结果缺少内容导览")
    sections = payload.get("sections")
    if not sections and payload.get("chapter_title"):
        sections = [{"title": payload.get("chapter_title"), "summary": payload.get("chapter_summary"), "knowledge_points": payload.get("knowledge_points")}]
    if not isinstance(sections, list) or not sections or len(sections) > 20:
        raise ValueError("聚合结果缺少有效章节")
    for section in sections:
        if not str(section.get("title", "")).strip() or not str(section.get("summary", "")).strip():
            raise ValueError("聚合结果缺少章节标题或摘要")
        rows = section.get("knowledge_points")
        if not isinstance(rows, list) or not rows:
            raise ValueError("聚合结果缺少知识点")
        for row in rows:
            if not str(row.get("statement", "")).strip() or len(str(row.get("explanation", "")).strip()) < 8:
                raise ValueError("聚合知识点缺少有效陈述或解释")
            refs = row.get("source_point_ids")
            if not isinstance(refs, list) or not refs or any(ref not in point_ids for ref in refs):
                raise ValueError("聚合知识点引用了不存在的来源 ID")


def _payload_sections(payload: dict) -> list[dict]:
    if payload.get("sections"):
        return payload["sections"]
    return [{
        "title": payload["chapter_title"], "summary": payload["chapter_summary"],
        "knowledge_points": payload["knowledge_points"],
    }]


def aggregate_documents(config: AppConfig, results: list[dict], qwen_settings: dict) -> dict:
    if len(results) < 2:
        raise ValueError("至少需要两个已完成视频才能聚合")
    documents = []
    markdown_paths = []
    for result in results:
        manifest_path = Path(result["manifest"])
        document_path = manifest_path.parent / "knowledge" / "document.json"
        markdown_path = Path(result["markdown"])
        if not document_path.is_file() or not markdown_path.is_file():
            raise FileNotFoundError("聚合所需的结构化文档或 Markdown 不完整")
        documents.append(json.loads(document_path.read_text(encoding="utf-8")))
        markdown_paths.append(markdown_path)
    source, point_map = _aggregate_source(documents)
    budget = qwen_settings.get("budget", {})
    max_chars = int(budget.get("max_input_chars", 60000))
    if len(source) > max_chars:
        raise RuntimeError(f"聚合输入共 {len(source)} 字符，超过云端上限 {max_chars}；未发送请求")
    models = list(qwen_settings.get("_runtime_models") or qwen_settings.get("default_models", []))
    max_calls = min(int(qwen_settings.get("_runtime_max_calls", 1)), int(budget.get("max_calls_per_video", 1)))
    content_level = str(qwen_settings.get("content_level", "推荐"))
    prompt = f"""你是课程资料总编。下面是多个连续或相关视频形成的结构化课程笔记。
请去除重复、合并同义内容、梳理前置概念与后续应用，把它们整合成可直接复习的多章节课程讲义。当前内容档位为“{content_level}”，应尽量保留来源中的解释、步骤、案例、条件和易错点，不能压缩成一个大章节。只允许依据输入内容，不补充外部知识。
输出严格 JSON：
{{"document_title":"聚合资料标题","overview":"2-4 句内容导览","learning_objectives":["学习目标"],"sections":[{{"title":"逻辑章节标题","summary":"章节摘要","knowledge_points":[{{"statement":"知识点标题","explanation":"完整解释","details":["补充细节"],"steps":["步骤"],"examples":["课程案例"],"conditions":["适用条件或边界"],"pitfalls":["易错点"],"editorial_note":"仅依据来源进行的逻辑整理，没有则为空字符串","review_tip":"一句话复习提示","source_point_ids":["point_0001"]}}]}}],"review":{{"knowledge_thread":"跨视频知识主线","checklist":["关键规则"],"open_questions":["来源尚未讲清的问题"]}}}}
每个知识点必须引用真实 source_point_ids；允许一个知识点引用多个来源。不要输出 JSON 之外的文字。

输入资料：
{source}"""
    client = FallbackChatClient(
        api_key=qwen_settings.get("_runtime_api_key", ""),
        base_url=qwen_settings.get("_runtime_base_url", ""),
        models=models[:max_calls],
        timeout=float(qwen_settings.get("timeout_seconds", 90.0)),
    )
    payload, model, attempts, usage = client.create_json(
        messages=[{"role": "user", "content": prompt}], temperature=0.1,
        max_tokens=int(budget.get("max_output_tokens", 5000)),
        validator=lambda value: _validate_aggregate(value, set(point_map)),
    )
    sections = []
    all_links = []
    all_figures = []
    for source_section in _payload_sections(payload):
        knowledge_points = []
        section_figures = []
        for row in source_section["knowledge_points"]:
            links = []
            figures = []
            for point_id in row["source_point_ids"]:
                for link in point_map[point_id]["links"]:
                    if link.get("url") and link not in links:
                        links.append(link)
                for figure in point_map[point_id].get("figures", []):
                    if figure.get("path") and all(figure.get("path") != item.get("path") for item in figures):
                        figures.append(figure)
            all_links.extend(link for link in links if link not in all_links)
            section_figures.extend(figure for figure in figures if figure not in section_figures)
            all_figures.extend(figure for figure in figures if figure not in all_figures)
            knowledge_points.append({
                "statement": str(row["statement"]).strip(),
                "explanation": str(row["explanation"]).strip(),
                "details": [str(item).strip() for item in (row.get("details") or []) if str(item).strip()],
                "steps": [str(item).strip() for item in (row.get("steps") or []) if str(item).strip()],
                "examples": [str(item).strip() for item in (row.get("examples") or []) if str(item).strip()],
                "conditions": [str(item).strip() for item in (row.get("conditions") or []) if str(item).strip()],
                "pitfalls": [str(item).strip() for item in (row.get("pitfalls") or []) if str(item).strip()],
                "editorial_note": str(row.get("editorial_note", "")).strip(),
                "review_tip": str(row.get("review_tip", "")).strip(),
                "source_segment_ids": [], "start_seconds": 0, "end_seconds": 0,
                "source_label": links[0]["label"] if links else "多视频来源",
                "source_url": links[0]["url"] if links else "", "source_links": links,
                "figures": figures,
            })
        sections.append({
            "title": str(source_section["title"]).strip(), "summary": str(source_section["summary"]).strip(),
            "start_seconds": 0, "end_seconds": 0, "knowledge_points": knowledge_points, "figures": section_figures,
        })
    video_ids = [str(doc.get("metadata", {}).get("video_id", "")) for doc in documents]
    fingerprint = json.dumps({
        "version": _AGGREGATE_GENERATOR_VERSION,
        "sources": [{"video_id": video_id, "generator_version": doc.get("generator_version"), "sections": doc.get("sections", [])} for video_id, doc in zip(video_ids, documents)],
    }, ensure_ascii=False, sort_keys=True, default=str)
    aggregate_id = "aggregate-" + hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:12]
    review = payload.get("review") if isinstance(payload.get("review"), dict) else {}
    document = {
        "schema_version": 1, "generator_version": _AGGREGATE_GENERATOR_VERSION, "generated_at": now_iso(), "mode": "cloud_aggregate",
        "metadata": {
            "video_id": aggregate_id, "title": payload["document_title"],
            "document_title": payload["document_title"], "source_video": "multiple",
            "duration_seconds": sum(float(doc.get("metadata", {}).get("duration_seconds", 0)) for doc in documents),
            "duration_label": f"{len(documents)} 个视频",
        },
        "overview": payload["overview"],
        "learning_objectives": [str(item).strip() for item in (payload.get("learning_objectives") or []) if str(item).strip()],
        "sections": sections, "figures": all_figures,
        "transcript": [], "notice": "本资料由多个视频的已生成知识文档二次聚合，请通过来源链接回看核对。",
        "review": {
            "knowledge_thread": str(review.get("knowledge_thread", "")).strip(),
            "checklist": [str(item).strip() for item in (review.get("checklist") or []) if str(item).strip()],
            "open_questions": [str(item).strip() for item in (review.get("open_questions") or []) if str(item).strip()],
        },
        "model": model, "model_attempts": [item.__dict__ for item in attempts],
        "cloud_usage": {**usage, "source_chars": len(source)},
        "source_markdown": [str(path) for path in markdown_paths], "source_video_ids": video_ids,
        "source_links": all_links, "render_options": {"include_full_transcript": False},
    }
    output_dir = config.path("paths", "output_dir") / aggregate_id
    title = safe_name(str(payload["document_title"]))
    document_json = output_dir / "document.json"
    markdown = output_dir / f"{title}.md"
    docx = output_dir / f"{title}.docx"
    pdf = output_dir / f"{title}.pdf"
    write_json(document_json, document)
    render_markdown(document, markdown, False)
    render_docx(document_json, docx, config.root)
    pdf_mode = convert_docx_to_pdf(docx, pdf, document)
    return {
        "video_id": aggregate_id, "manifest": document_json, "markdown": markdown,
        "docx": docx, "pdf": pdf, "pdf_mode": pdf_mode, "mode": "cloud_aggregate",
        "model": model, "model_attempts": document["model_attempts"], "cloud_usage": document["cloud_usage"],
    }
