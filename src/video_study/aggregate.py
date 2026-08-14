from __future__ import annotations

import hashlib
import json
import math
import re
import traceback
import uuid
from pathlib import Path
from urllib.parse import quote

from .config import AppConfig
from .providers import FallbackChatClient
from .render import DocumentAdapter
from .utils import now_iso, safe_name
from .execution.artifacts import FileArtifactStore, WorkspaceCatalog, WorkspaceLayout, read_document_v2
from .execution.events import RunEventJournal

_AGGREGATE_GENERATOR_VERSION = 2


_AGGREGATE_SCHEMA = """{"document_title":"聚合资料标题","overview":"2-4 句内容导览","learning_objectives":["学习目标"],"sections":[{"title":"逻辑章节标题","summary":"章节摘要","knowledge_points":[{"statement":"知识点标题","explanation":"完整解释","details":["补充细节"],"steps":["步骤"],"examples":["课程案例"],"conditions":["适用条件或边界"],"pitfalls":["易错点"],"editorial_note":"仅依据来源进行的逻辑整理，没有则为空字符串","review_tip":"一句话复习提示","source_point_ids":["point_0001"]}]}],"review":{"knowledge_thread":"跨视频知识主线","checklist":["关键规则"],"open_questions":["来源尚未讲清的问题"]}}"""


def _aggregate_prompt(source: str, content_level: str, *, intermediate: bool = False) -> str:
    scope = (
        "这是较大输入的一个连续分批。只整理当前批次，保留原始 source_point_ids，"
        "不得将它们改成新编号。"
        if intermediate else
        "请去除重复、合并同义内容、梳理前置概念与后续应用，把它们整合成可直接复习的多章节课程讲义。"
    )
    return f"""你是课程资料总编。下面是多个连续或相关视频形成的结构化课程笔记。
{scope}当前内容档位为“{content_level}”，应尽量保留来源中的解释、步骤、案例、条件和易错点，不能压缩成一个大章节。只允许依据输入内容，不补充外部知识。
输出严格 JSON：
{_AGGREGATE_SCHEMA}
每个知识点必须引用真实 source_point_ids；允许一个知识点引用多个来源。不要输出 JSON 之外的文字。

输入资料：
{source}"""


def _split_source_for_prompts(source: str, content_level: str, max_chars: int) -> list[str]:
    """按行保留完整知识点，使每个分批请求都不超过云端字符上限。"""
    overhead = len(_aggregate_prompt("", content_level, intermediate=True))
    capacity = max_chars - overhead
    if capacity <= 0:
        raise RuntimeError("聚合提示词已超过云端输入上限；未发送请求")
    lines = source.splitlines()
    target = max(1, math.ceil(len(source) / math.ceil(len(source) / capacity)))
    split_threshold = min(capacity, target + max((len(line) + 1 for line in lines), default=0))
    chunks: list[str] = []
    current: list[str] = []
    for line in lines:
        candidate = "\n".join([*current, line]).strip()
        if current and len(candidate) > split_threshold:
            chunks.append("\n".join(current).strip())
            current = [line]
        else:
            current.append(line)
        if len(_aggregate_prompt("\n".join(current).strip(), content_level, intermediate=True)) > max_chars:
            raise RuntimeError("聚合中存在单个知识点超过云端输入上限；未发送请求")
    if current:
        chunks.append("\n".join(current).strip())
    return [chunk for chunk in chunks if chunk]


def _source_point_ids(source: str) -> set[str]:
    return set(re.findall(r"\[(point_\d+)\]", source))


def _usage_add(total: dict[str, int], usage: dict) -> None:
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        total[key] = int(total.get(key, 0)) + int(usage.get(key, 0) or 0)


def _source_link(document: dict, point: dict, title: str) -> dict:
    video_id = str(document.get("metadata", {}).get("video_id", ""))
    refs = point.get("source_refs", {}) if isinstance(point.get("source_refs"), dict) else {}
    start = int(float(refs.get("start_seconds", point.get("start_seconds", 0)) or 0))
    current_url = str(refs.get("url", point.get("source_url", "")))
    url = current_url if current_url.startswith("video-study://play/") else f"video-study://play/{quote(video_id, safe='')}?t={start}"
    return {"label": f"{title} · {refs.get('label', point.get('source_label', ''))}", "url": url}


def _point_source_text(point: dict) -> str:
    if point.get("content_blocks"):
        parts: list[str] = []
        for block in point["content_blocks"]:
            if block.get("text"):
                parts.append(str(block["text"]))
            parts.extend(str(item) for item in block.get("items", []) if str(item).strip())
        return "；".join(item.strip() for item in parts if item.strip())
    parts = [str(point.get("explanation", ""))]
    for field in ("details", "steps", "examples", "conditions", "pitfalls"):
        parts.extend(str(item) for item in (point.get(field) or []) if str(item).strip())
    if point.get("editorial_note"):
        parts.append(f"整理说明：{point['editorial_note']}")
    return "；".join(item.strip() for item in parts if item.strip())


def _aggregate_source(documents: list[dict]) -> tuple[str, dict[str, dict]]:
    from .knowledge.adapter import v1_to_v2
    documents = [
        document if int(document.get("schema_version", 1) or 1) == 2 else v1_to_v2(document)
        for document in documents
    ]
    lines: list[str] = []
    points: dict[str, dict] = {}
    counter = 0
    for doc_index, document in enumerate(documents, start=1):
        title = document.get("metadata", {}).get("document_title") or document.get("metadata", {}).get("title", f"视频 {doc_index}")
        video_id = str(document.get("metadata", {}).get("video_id", ""))
        lines.extend([f"## 视频 {doc_index}：{title}", str(document.get("overview", ""))])
        for section in document.get("sections", []):
            lines.append(f"### {section.get('title', '')}")
            for point in section.get("knowledge_points", []):
                counter += 1
                point_id = f"point_{counter:04d}"
                links = [_source_link(document, point, title)]
                point_figures = list(point.get("figures", []))
                refs = point.get("source_refs", {}) if isinstance(point.get("source_refs"), dict) else {}
                # V2: 不再按时间匹配 section figures，只使用 point 已有的 figures
                points[point_id] = {
                    "title": title,
                    "point": point,
                    "links": links,
                    "figures": point_figures,
                    "video_id": video_id,
                    "start_seconds": float(refs.get("start_seconds", point.get("start_seconds", 0))),
                    "end_seconds": float(refs.get("end_seconds", point.get("end_seconds", 0))),
                }
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


def _aggregate_event(settings: dict, code: str, message: str, **details) -> None:
    sink = settings.get("_aggregate_event_sink")
    if sink:
        sink({
            "type": "aggregate_state",
            "step_id": "aggregate",
            "stage": "aggregate",
            "level": details.pop("level", "info"),
            "code": code,
            "message": message,
            **details,
        })


def aggregate_documents(config: AppConfig, results: list[dict], qwen_settings: dict) -> dict:
    run_id = uuid.uuid4().hex
    ordered_sources = [{
        "position": index,
        "video_id": str(result.get("video_id", "")),
        "manifest": str(result.get("manifest", "")),
        "markdown": str(result.get("markdown", "")),
    } for index, result in enumerate(results, start=1)]
    identity = json.dumps(
        {"version": _AGGREGATE_GENERATOR_VERSION, "sources": ordered_sources},
        ensure_ascii=False, sort_keys=True,
    )
    work_id = "aggregate-work-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    paths = config.raw.get("paths", {})
    workspace_root = (config.root / str(paths.get("workspace_dir", "workspace"))).resolve()
    output_root = config.path("paths", "output_dir")
    layout = WorkspaceLayout(workspace_root, work_id, output_root)
    journal = RunEventJournal(layout, run_id)
    journal.start({
        "work_type": "aggregate",
        "ordered_sources": ordered_sources,
        "generator_version": _AGGREGATE_GENERATOR_VERSION,
        "cloud": {
            "endpoint": qwen_settings.get("_runtime_base_url", ""),
            "models": list(qwen_settings.get("_runtime_models") or qwen_settings.get("default_models", [])),
            "max_calls": qwen_settings.get("_runtime_max_calls", 1),
            "budget": dict(qwen_settings.get("budget", {})),
        },
    })
    settings = dict(qwen_settings)
    settings["_aggregate_event_sink"] = journal.publish
    try:
        value = _aggregate_documents_impl(config, results, settings)
        journal.finish("succeeded", outputs={
            key: value.get(key) for key in ("video_id", "manifest", "markdown", "docx", "pdf", "pdf_mode", "mode")
        })
        value["workspace"] = layout.video_root
        value["run_id"] = run_id
        value["runtime_events"] = list(journal.events)
        value["degradations"] = [row for row in journal.events if row.get("level") in {"warning", "error"}]
        return value
    except BaseException as exc:
        journal.finish("failed", error=exc, traceback_text=traceback.format_exc())
        raise


def _aggregate_documents_impl(config: AppConfig, results: list[dict], qwen_settings: dict) -> dict:
    _aggregate_event(qwen_settings, "aggregate_inputs_started", "开始校验聚合输入", source_count=len(results))
    if len(results) < 2:
        raise ValueError("至少需要两个已完成视频才能聚合")
    documents = []
    markdown_paths = []
    for result in results:
        manifest_path = Path(result["manifest"])
        document_path = WorkspaceCatalog(manifest_path.parent.parent).document_for_manifest(manifest_path)
        markdown_path = Path(result["markdown"])
        if not document_path.is_file() or not markdown_path.is_file():
            raise FileNotFoundError("聚合所需的结构化文档或 Markdown 不完整")
        documents.append(read_document_v2(document_path))
        markdown_paths.append(markdown_path)
    _aggregate_event(qwen_settings, "aggregate_inputs_completed", "聚合输入校验完成", source_count=len(documents))
    source, point_map = _aggregate_source(documents)
    budget = qwen_settings.get("budget", {})
    max_chars = int(budget.get("max_input_chars", 60000))
    models = list(qwen_settings.get("_runtime_models") or qwen_settings.get("default_models", []))
    max_calls = min(int(qwen_settings.get("_runtime_max_calls", 1)), int(budget.get("max_calls_per_video", 1)))
    content_level = str(qwen_settings.get("content_level", "推荐"))
    from .providers import ensure_cloud_request_budget
    request_budget = ensure_cloud_request_budget(qwen_settings)
    request_budget.max_requests = min(request_budget.max_requests, max_calls)
    prompt = _aggregate_prompt(source, content_level)
    chunks: list[str] = []
    if len(prompt) > max_chars:
        chunks = _split_source_for_prompts(source, content_level, max_chars)
        required_calls = len(chunks) + 1
        if required_calls > request_budget.max_requests:
            raise RuntimeError(
                f"聚合输入共 {len(source)} 字符，需要 {required_calls} 次分批请求，"
                f"但本次授权上限为 {request_budget.max_requests}；未发送请求"
            )
    client = FallbackChatClient(
        api_key=qwen_settings.get("_runtime_api_key", ""),
        base_url=qwen_settings.get("_runtime_base_url", ""),
        models=models[:max_calls],
        timeout=float(qwen_settings.get("timeout_seconds", 90.0)),
    )
    _aggregate_event(
        qwen_settings, "aggregate_cloud_started", "开始云端聚合请求",
        source_chars=len(source), prompt_chars=len(prompt), models=models[:max_calls], max_calls=max_calls,
        strategy="hierarchical" if chunks else "single", planned_requests=len(chunks) + 1 if chunks else 1,
    )
    attempts = []
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    if chunks:
        intermediate_payloads = []
        for index, chunk in enumerate(chunks, start=1):
            batch_prompt = _aggregate_prompt(chunk, content_level, intermediate=True)
            _aggregate_event(
                qwen_settings, "aggregate_batch_started", f"开始整理聚合分批 {index}/{len(chunks)}",
                batch_index=index, batch_count=len(chunks), source_chars=len(chunk), prompt_chars=len(batch_prompt),
            )
            batch_payload, batch_model, batch_attempts, batch_usage = client.create_json(
                messages=[{"role": "user", "content": batch_prompt}], temperature=0.1,
                max_tokens=min(int(budget.get("max_output_tokens", 5000)), 3200),
                validator=lambda value, ids=_source_point_ids(chunk): _validate_aggregate(value, ids),
                request_budget=request_budget, stage=f"aggregate_batch_{index}",
                cancel_check=qwen_settings.get("_cancel_check"),
            )
            intermediate_payloads.append(batch_payload)
            attempts.extend(batch_attempts)
            _usage_add(usage, batch_usage)
            _aggregate_event(
                qwen_settings, "aggregate_batch_completed", f"聚合分批 {index}/{len(chunks)} 整理完成",
                batch_index=index, batch_count=len(chunks), model=batch_model,
                attempts=[item.__dict__ for item in batch_attempts], usage=batch_usage,
            )
        merged_source = "\n\n".join(
            f"## 分批整理结果 {index}\n{json.dumps(item, ensure_ascii=False, separators=(',', ':'))}"
            for index, item in enumerate(intermediate_payloads, start=1)
        )
        prompt = _aggregate_prompt(merged_source, content_level)
        if len(prompt) > max_chars:
            raise RuntimeError(
                f"分批整理结果共 {len(prompt)} 字符，仍超过云端上限 {max_chars}；"
                "已保留分批日志，未发送最终合并请求"
            )
        _aggregate_event(
            qwen_settings, "aggregate_merge_started", "开始合并聚合分批结果",
            batch_count=len(chunks), prompt_chars=len(prompt),
        )
    payload, model, final_attempts, final_usage = client.create_json(
        messages=[{"role": "user", "content": prompt}], temperature=0.1,
        max_tokens=int(budget.get("max_output_tokens", 5000)),
        validator=lambda value: _validate_aggregate(value, set(point_map)),
        request_budget=request_budget, stage="aggregate_merge" if chunks else "aggregate",
        cancel_check=qwen_settings.get("_cancel_check"),
    )
    attempts.extend(final_attempts)
    _usage_add(usage, final_usage)
    _aggregate_event(
        qwen_settings, "aggregate_cloud_completed", "云端聚合请求完成",
        model=model, attempts=[item.__dict__ for item in attempts], usage=usage,
        strategy="hierarchical" if chunks else "single", requests_used=request_budget.requests_used,
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
            content_blocks = []
            for point_id in row["source_point_ids"]:
                for link in point_map[point_id]["links"]:
                    if link.get("url") and link not in links:
                        links.append(link)
                for figure in point_map[point_id].get("figures", []):
                    if figure.get("path") and all(figure.get("path") != item.get("path") for item in figures):
                        figures.append(figure)
                for block in point_map[point_id]["point"].get("content_blocks", []):
                    if block not in content_blocks:
                        content_blocks.append(block)
            primary_id = row["source_point_ids"][0]
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
                # V2: 保留原始时间戳而非清零
                "source_segment_ids": [],
                "start_seconds": point_map[primary_id]["start_seconds"],
                "end_seconds": point_map[primary_id]["end_seconds"],
                "source_video_id": point_map[primary_id].get("video_id", ""),
                "source_label": links[0]["label"] if links else "多视频来源",
                "source_url": links[0]["url"] if links else "", "source_links": links,
                "figures": figures,
                "content_blocks": content_blocks,
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
        "cloud_usage": {
            **usage, "source_chars": len(source), "strategy": "hierarchical" if chunks else "single",
            "batch_count": len(chunks), "requests_used": request_budget.requests_used,
        },
        "source_markdown": [str(path) for path in markdown_paths], "source_video_ids": video_ids,
        "source_links": all_links, "render_options": {"include_full_transcript": False},
    }
    from .knowledge.adapter import v1_to_v2
    document = v1_to_v2(document)
    output_dir = config.path("paths", "output_dir") / aggregate_id
    title = safe_name(str(payload["document_title"]))
    document_json = output_dir / "document.json"
    markdown = output_dir / f"{title}.md"
    docx = output_dir / f"{title}.docx"
    pdf = output_dir / f"{title}.pdf"
    FileArtifactStore().write_document_v2(document_json, document)
    document_port = DocumentAdapter(config.root, include_transcript=False)
    cancel_check = qwen_settings.get("_cancel_check") or (lambda: False)
    _aggregate_event(qwen_settings, "aggregate_render_started", "开始生成聚合 Markdown、Word 和 PDF")
    document_port.render_markdown(document, markdown)
    document_port.render_word(document_json, docx, cancel_check=cancel_check)
    pdf_mode = document_port.render_pdf(document, docx, pdf, cancel_check=cancel_check)
    _aggregate_event(
        qwen_settings, "aggregate_render_completed", "聚合文档生成完成",
        markdown=markdown, docx=docx, pdf=pdf, pdf_mode=pdf_mode,
    )
    return {
        "video_id": aggregate_id, "manifest": document_json, "markdown": markdown,
        "docx": docx, "pdf": pdf, "pdf_mode": pdf_mode, "mode": "cloud_aggregate",
        "model": model, "model_attempts": document["model_attempts"], "cloud_usage": document["cloud_usage"],
    }
