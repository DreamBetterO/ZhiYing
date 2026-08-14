from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from .asr import transcribe
from .config import AppConfig
from .frames import extract_keyframes
from .media import create_manifest, discover_videos, extract_audio
from .render import convert_docx_to_pdf, render_docx, render_markdown
from .summarize import build_document
from .utils import TaskCancelled, ensure_not_cancelled, now_iso, safe_name, write_json


def _stage(manifest_path: Path, manifest: dict, name: str, **details) -> None:
    manifest["stages"][name] = {"completed_at": now_iso(), **details}
    write_json(manifest_path, manifest)


def process_video(
    config: AppConfig,
    video: Path,
    force: bool = False,
    force_summary: bool = False,
    cloud_summary: bool | None = None,
    force_asr: bool = False,
    qwen_settings: dict | None = None,
    asr_settings: dict | None = None,
    progress: Callable[[str, str, int], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> dict:
    def report(stage: str, message: str, percent: int) -> None:
        if progress:
            progress(stage, message, percent)
    ensure_not_cancelled(cancel_check)
    work_root = config.path("paths", "workspace_dir")
    output_root = config.path("paths", "output_dir")
    work_dir, manifest = create_manifest(video, work_root)
    manifest_path = work_dir / "manifest.json"
    print(f"[video] {video.name}")

    report("audio", "正在提取或复用音频", 10)
    audio = extract_audio(video, work_dir / "audio" / "audio.flac", force, cancel_check)
    _stage(manifest_path, manifest, "audio", path=str(audio))

    model_dir = config.path("paths", "model_dir")
    if not model_dir.exists():
        raise FileNotFoundError(f"ASR 模型目录不存在：{model_dir}\n请先使用 huggingface-cli download 下载模型。")
    transcript_json = work_dir / "transcript" / "transcript.json"
    report("asr", "正在执行本地语音识别", 30)
    runtime_asr = dict(asr_settings or config.raw["asr"])
    runtime_asr["_config_root"] = str(config.root)
    runtime_asr["_duration_seconds"] = manifest["duration_seconds"]
    runtime_asr["_cancel_check"] = cancel_check
    runtime_asr["_progress_callback"] = lambda fraction: report(
        "asr", "正在执行本地语音识别", 20 + int(max(0.0, min(1.0, fraction)) * 40)
    )
    engine_chain = list(runtime_asr.pop("_engine_chain", [runtime_asr.get("engine", "faster-whisper")]))
    transcript = None
    last_error = None
    for engine_index, engine in enumerate(engine_chain):
        runtime_asr["engine"] = engine
        report("asr", f"正在使用 {engine} 执行语音识别", 20)
        try:
            transcript = transcribe(
                audio, transcript_json, work_dir / "transcript" / "transcript.srt",
                model_dir, runtime_asr, force or force_asr, context=manifest["title"],
            )
            break
        except TaskCancelled:
            raise
        except Exception as exc:
            last_error = exc
            if engine_index + 1 < len(engine_chain):
                report("asr", f"{engine} 不可用，正在降级到 {engine_chain[engine_index + 1]}", 20)
    if transcript is None:
        raise last_error or RuntimeError("所有语音模型均不可用")
    _stage(manifest_path, manifest, "transcript", path=str(transcript_json), segments=len(transcript["segments"]))

    frame_settings = dict(config.raw["frames"])
    if transcript.get("segments"):
        frame_settings["content_start_seconds"] = (
            float(transcript["segments"][0]["start_seconds"])
            + float(frame_settings.get("content_start_padding_seconds", 30.0))
        )
    report("frames", "正在提取和筛选关键画面", 62)
    ensure_not_cancelled(cancel_check)
    frames = extract_keyframes(
        video, work_dir / "images", manifest["duration_seconds"], frame_settings, force, cancel_check
    )
    _stage(manifest_path, manifest, "frames", count=len(frames["frames"]))

    document_json = work_dir / "knowledge" / "document.json"
    report("knowledge", "正在生成结构化初稿或云端精炼", 78)
    ensure_not_cancelled(cancel_check)
    summary_settings = dict(qwen_settings or config.raw["qwen"])
    summary_settings["content_level"] = config.raw["qwen"].get("content_level", config.raw["render"].get("content_level", "推荐"))
    summary_settings["budget"] = {**summary_settings.get("budget", {}), **config.raw["qwen"].get("budget", {})}
    summary_settings["timeout_seconds"] = config.raw["qwen"].get("timeout_seconds", summary_settings.get("timeout_seconds", 90.0))
    document = build_document(
        manifest, transcript, frames, document_json,
        summary_settings, config.raw["render"], force or force_summary, cloud_summary,
    )
    _stage(manifest_path, manifest, "knowledge", path=str(document_json), mode=document["mode"])

    output_dir = output_root / manifest["video_id"]
    title = safe_name(manifest["title"])
    markdown = output_dir / f"{title}.md"
    docx = output_dir / f"{title}.docx"
    pdf = output_dir / f"{title}.pdf"
    report("render", "正在生成 Markdown、Word 和 PDF", 90)
    ensure_not_cancelled(cancel_check)
    render_markdown(document, markdown, bool(config.raw["render"].get("include_full_transcript", True)))
    render_docx(document_json, docx, config.root)
    pdf_mode = convert_docx_to_pdf(docx, pdf, document)
    _stage(manifest_path, manifest, "render", markdown=str(markdown), docx=str(docx), pdf=str(pdf), pdf_mode=pdf_mode)
    print(f"[done] {output_dir}")
    report("completed", "处理完成", 100)
    return {
        "video_id": manifest["video_id"], "manifest": manifest_path, "markdown": markdown,
        "docx": docx, "pdf": pdf, "pdf_mode": pdf_mode, "mode": document.get("mode"),
        "model": document.get("model"), "model_attempts": document.get("model_attempts", []),
        "cloud_usage": document.get("cloud_usage", {}),
    }


def run_all(
    config: AppConfig,
    video: str | None = None,
    force: bool = False,
    force_summary: bool = False,
    cloud_summary: bool | None = None,
    force_asr: bool = False,
    qwen_settings: dict | None = None,
    asr_settings: dict | None = None,
    progress: Callable[[str, str, int], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> list[dict]:
    videos = [Path(video).resolve()] if video else discover_videos(config.path("paths", "input_dir"))
    if not videos:
        raise FileNotFoundError("输入目录中没有找到 MP4 文件")
    return [
        process_video(
            config,
            item,
            force=force,
            force_summary=force_summary,
            cloud_summary=cloud_summary,
            force_asr=force_asr,
            qwen_settings=qwen_settings,
            asr_settings=asr_settings,
            progress=progress,
            cancel_check=cancel_check,
        )
        for item in videos
    ]
