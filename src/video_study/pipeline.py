from __future__ import annotations

from pathlib import Path
from typing import Callable

from .config import AppConfig
from .execution.bootstrap import discover_configured_videos, run_compatible_pipeline
from .progress import ProgressEvent


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
    task_progress: Callable[[ProgressEvent], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    event: Callable[[dict], None] | None = None,
) -> dict:
    """兼容门面：旧公开参数只在此翻译，生产编排由 PipelineRunner 执行。"""
    return run_compatible_pipeline(
        config,
        Path(video),
        force=force,
        force_summary=force_summary,
        cloud_summary=cloud_summary,
        force_asr=force_asr,
        qwen_settings=qwen_settings,
        asr_settings=asr_settings,
        progress=progress,
        task_progress=task_progress,
        cancel_check=cancel_check,
        event=event,
    )


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
    task_progress: Callable[[ProgressEvent], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    event: Callable[[dict], None] | None = None,
) -> list[dict]:
    videos = [Path(video).resolve()] if video else discover_configured_videos(config)
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
            task_progress=task_progress,
            cancel_check=cancel_check,
            event=event,
        )
        for item in videos
    ]
