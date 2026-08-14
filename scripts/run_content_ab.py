from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path

from video_study.config import load_config
from video_study.desktop import _runtime_cloud_config, config_with_content_level
from video_study.render import convert_docx_to_pdf, render_docx, render_markdown
from video_study.summarize import build_document
from video_study.utils import now_iso, safe_name, write_json


def _serializable_result(result: dict) -> dict:
    return {
        key: str(value) if isinstance(value, Path) else value
        for key, value in result.items()
    }


def _document_metrics(document: dict, markdown: Path) -> dict:
    points = [
        point
        for section in document.get("sections", [])
        for point in section.get("knowledge_points", [])
    ]
    return {
        "sections": len(document.get("sections", [])),
        "knowledge_points": len(points),
        "points_with_steps": sum(bool(point.get("steps")) for point in points),
        "points_with_examples": sum(bool(point.get("examples")) for point in points),
        "points_with_conditions": sum(bool(point.get("conditions")) for point in points),
        "points_with_pitfalls": sum(bool(point.get("pitfalls")) for point in points),
        "points_with_editorial_notes": sum(bool(point.get("editorial_note")) for point in points),
        "linked_figures": sum(len(point.get("figures", [])) for point in points),
        "markdown_chars": len(markdown.read_text(encoding="utf-8")),
    }


def _drop_early_non_teaching_figures(document: dict) -> None:
    for section in document.get("sections", []):
        linked = []
        for point in section.get("knowledge_points", []):
            point["figures"] = [
                figure for figure in point.get("figures", [])
                if float(figure.get("timestamp_seconds", 0.0)) >= 60.0
            ]
            linked.extend(point["figures"])
        section["figures"] = linked
    document["figures"] = [
        figure for figure in document.get("figures", [])
        if float(figure.get("timestamp_seconds", 0.0)) >= 60.0
    ]


def _refresh_completed_profile(config, profile: dict) -> None:
    paths = profile["comparison_files"]
    manifest = Path(paths["manifest"])
    document = json.loads(manifest.read_text(encoding="utf-8"))
    _drop_early_non_teaching_figures(document)
    write_json(manifest, document)
    markdown, docx, pdf = Path(paths["markdown"]), Path(paths["docx"]), Path(paths["pdf"])
    render_markdown(document, markdown, False)
    render_docx(manifest, docx, config.root)
    convert_docx_to_pdf(docx, pdf, document)
    profile["metrics"] = _document_metrics(document, markdown)


def _cached_inputs(config, video: Path) -> tuple[Path, dict, dict, dict]:
    for manifest_path in config.path("paths", "workspace_dir").glob("*/manifest.json"):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        source = Path(str(manifest.get("source_path", "")))
        if source.is_file() and source.resolve() == video.resolve():
            transcript_path = manifest_path.parent / "transcript" / "transcript.json"
            frames_path = manifest_path.parent / "images" / "keyframes.json"
            if not transcript_path.is_file() or not frames_path.is_file():
                raise FileNotFoundError(f"缓存不完整：{manifest_path.parent}")
            return (
                manifest_path,
                manifest,
                json.loads(transcript_path.read_text(encoding="utf-8")),
                json.loads(frames_path.read_text(encoding="utf-8")),
            )
    raise FileNotFoundError(f"没有找到视频对应的 manifest：{video}")


def _run_cached_knowledge(config, video: Path, qwen_settings: dict, level: str, experiment_root: Path) -> dict:
    """只读取 manifest/transcript/keyframes，完全绕过音频、ASR 和抽帧阶段。"""
    manifest_path, manifest, transcript, frames = _cached_inputs(config, video)
    document_path = manifest_path.parent / "knowledge" / f"document-experiment-{level}.json"
    document = json.loads(document_path.read_text(encoding="utf-8")) if document_path.is_file() else None
    if not document or document.get("mode") != "cloud_summary" or document.get("render_options", {}).get("content_level") != level:
        document = build_document(
            manifest, transcript, frames, document_path, qwen_settings, config.raw["render"],
            force=False, cloud_override=True,
        )
    output_dir = experiment_root / level / "sources" / manifest["video_id"]
    title = safe_name(manifest["title"])
    markdown = output_dir / f"{title}.md"
    docx = output_dir / f"{title}.docx"
    pdf = output_dir / f"{title}.pdf"
    render_markdown(document, markdown, bool(config.raw["render"].get("include_full_transcript", True)))
    render_docx(document_path, docx, config.root)
    pdf_mode = convert_docx_to_pdf(docx, pdf, document)
    return {
        "video_id": manifest["video_id"], "manifest": manifest_path, "document_json": document_path, "markdown": markdown,
        "docx": docx, "pdf": pdf, "pdf_mode": pdf_mode, "mode": document.get("mode"),
        "model": document.get("model"), "model_attempts": document.get("model_attempts", []),
        "cloud_usage": document.get("cloud_usage", {}),
    }


def _local_aggregate(config, results: list[dict], profile_dir: Path, level: str) -> dict:
    documents = []
    for result in results:
        document_path = Path(result.get("document_json") or (Path(result["manifest"]).parent / "knowledge" / "document.json"))
        documents.append(json.loads(document_path.read_text(encoding="utf-8")))
    sections = []
    objectives = []
    figures = []
    checklist = []
    open_questions = []
    knowledge_threads = []
    for index, document in enumerate(documents, start=4):
        for item in document.get("learning_objectives", []):
            if item not in objectives:
                objectives.append(item)
        for section in document.get("sections", []):
            row = deepcopy(section)
            row["title"] = f"第{index}课 · {row['title']}"
            sections.append(row)
        for figure in document.get("figures", []):
            if figure.get("path") and all(figure.get("path") != item.get("path") for item in figures):
                figures.append(figure)
        review = document.get("review") or {}
        if review.get("knowledge_thread"):
            knowledge_threads.append(f"第{index}课：{review['knowledge_thread']}")
        for item in review.get("checklist", []):
            if item not in checklist:
                checklist.append(item)
        for item in review.get("open_questions", []):
            if item not in open_questions:
                open_questions.append(item)
    usage = {
        key: sum(int((result.get("cloud_usage") or {}).get(key, 0) or 0) for result in results)
        for key in ("prompt_tokens", "completion_tokens", "total_tokens", "source_chars")
    }
    title = f"本质线段中枢课程综合讲义（{level}）"
    document = {
        "schema_version": 1, "generator_version": 1, "generated_at": now_iso(), "mode": "cloud_aggregate",
        "metadata": {
            "video_id": f"course-notes-ab-{level}", "title": title, "document_title": title,
            "source_video": "multiple", "duration_seconds": sum(float(doc["metadata"].get("duration_seconds", 0)) for doc in documents),
            "duration_label": "3 个视频",
        },
        "overview": "本讲义依次整合第 4、5、6 课的云端课程笔记，覆盖本质线段中枢的形成确认、中枢取值与唯一选取。各章节保留原视频来源、课程案例、步骤、适用边界、易错点和同期关键画面，便于连续学习与回看核对。",
        "learning_objectives": objectives, "sections": sections, "figures": figures, "transcript": [],
        "notice": "本资料由三个视频的云端课程讲义按课程顺序本地整合，未额外调用聚合模型；请通过来源链接回看核对。",
        "review": {
            "knowledge_thread": "\n".join(knowledge_threads), "checklist": checklist, "open_questions": open_questions,
        },
        "model": "cloud-refined-sources/local-deterministic-aggregate", "model_attempts": [],
        "cloud_usage": usage, "source_video_ids": [doc["metadata"]["video_id"] for doc in documents],
        "render_options": {"include_full_transcript": False, "content_level": level},
    }
    _drop_early_non_teaching_figures(document)
    profile_dir.mkdir(parents=True, exist_ok=True)
    manifest = profile_dir / "document.json"
    markdown = profile_dir / f"{title}.md"
    docx = profile_dir / f"{title}.docx"
    pdf = profile_dir / f"{title}.pdf"
    write_json(manifest, document)
    render_markdown(document, markdown, False)
    render_docx(manifest, docx, config.root)
    pdf_mode = convert_docx_to_pdf(docx, pdf, document)
    return {
        "video_id": document["metadata"]["video_id"], "manifest": manifest, "markdown": markdown,
        "docx": docx, "pdf": pdf, "pdf_mode": pdf_mode, "mode": document["mode"],
        "model": document["model"], "model_attempts": [], "cloud_usage": usage,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="生成推荐/丰富两份云端综合课程讲义并记录用量")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--video-dir", default="视频")
    parser.add_argument("--confirm-cloud", action="store_true")
    args = parser.parse_args()
    if not args.confirm_cloud:
        raise SystemExit("该脚本会消耗云端额度；请在获得用户授权后显式添加 --confirm-cloud")

    config = load_config(args.config)
    qwen = config.raw["qwen"]
    key = os.getenv(qwen.get("api_key_env", "QWEN_API_KEY"), "").strip()
    base_url = os.getenv(qwen.get("base_url_env", "QWEN_BASE_URL"), qwen.get("default_base_url", "")).strip()
    chain = os.getenv(qwen.get("model_chain_env", "QWEN_MODEL_CHAIN"), "")
    models = [item.strip() for item in chain.split(",") if item.strip()] or list(qwen.get("default_models", []))
    runtime = _runtime_cloud_config({
        "api_key": key, "base_url": base_url, "llm_models": models, "allow_fallback": True,
    }, qwen)

    videos = sorted((config.root / args.video_dir).resolve().glob("*.mp4"))
    if len(videos) != 3:
        raise RuntimeError(f"实验要求恰好 3 个 MP4，实际找到 {len(videos)} 个")

    experiment_root = config.path("paths", "output_dir") / "experiments" / "course-notes-ab"
    report_path = experiment_root / "report.json"
    if report_path.is_file():
        report = json.loads(report_path.read_text(encoding="utf-8"))
    else:
        report = {
            "generated_at": now_iso(),
            "videos": [video.name for video in videos],
            "models": models,
            "profiles": [],
        }
    for level in ("推荐", "丰富"):
        completed_profile = next((item for item in report["profiles"] if item.get("level") == level), None)
        if completed_profile and Path(completed_profile["comparison_files"]["manifest"]).is_file():
            _refresh_completed_profile(config, completed_profile)
            write_json(report_path, report)
            print(f"[experiment] {level} 已完成，复用综合产物", flush=True)
            continue
        print(f"[experiment] 开始 {level}：逐视频云端课程讲义", flush=True)
        task_config = config_with_content_level(config, level)
        profile_runtime = dict(runtime)
        profile_runtime["content_level"] = level
        profile_runtime["_runtime_models"] = ["qwen3.7-plus"]
        profile_runtime["_runtime_max_calls"] = 1
        profile_runtime["timeout_seconds"] = 240
        profile_runtime["budget"] = {
            **profile_runtime.get("budget", {}), **task_config.raw["qwen"].get("budget", {}),
        }
        single_results = []
        for index, video in enumerate(videos, start=1):
            print(f"[experiment] {level} {index}/3：{video.name}", flush=True)
            result = _run_cached_knowledge(task_config, video, profile_runtime, level, experiment_root)
            if result.get("mode") != "cloud_summary":
                raise RuntimeError(f"{video.name} 未生成云端讲义，实际模式：{result.get('mode')}")
            single_results.append(result)

        print(f"[experiment] {level}：本地整合三课云端课程讲义", flush=True)
        profile_dir = experiment_root / level
        aggregate = _local_aggregate(task_config, single_results, profile_dir, level)
        source_manifest = Path(aggregate["manifest"])
        document = json.loads(source_manifest.read_text(encoding="utf-8"))
        copied = {kind: str(aggregate[kind]) for kind in ("manifest", "markdown", "docx", "pdf")}
        report["profiles"].append({
            "level": level,
            "single_results": [_serializable_result(item) for item in single_results],
            "aggregate": _serializable_result(aggregate),
            "comparison_files": copied,
            "metrics": _document_metrics(document, Path(aggregate["markdown"])),
        })
        write_json(report_path, report)
        print(f"[experiment] {level} 完成：{profile_dir}", flush=True)

    print(f"[experiment] A/B 全部完成：{report_path}", flush=True)


if __name__ == "__main__":
    main()
