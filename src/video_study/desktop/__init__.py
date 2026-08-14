from __future__ import annotations

from ..application.processing import DefaultProcessingService
from ..config import AppConfig
from .models import DesktopState, QueueItem, UiEvent
from .settings import (
    VISUAL_TEACHING_LABELS,
    VISUAL_TEACHING_LEVELS,
    config_with_content_level,
    config_with_visual_teaching_level,
    qwen_asr_ready,
    save_api_credentials,
    save_desktop_settings,
    validate_desktop_settings,
    validate_speech_models,
)

STAGE_LABELS = {
    "queued": "等待中", "source": "源文件", "audio": "音频", "asr": "语音识别",
    "transcript": "转写", "frames": "关键画面", "knowledge": "知识整理",
    "document": "文档组装", "visual": "视觉核验", "render": "文档生成",
    "source.probe": "1/15 源文件检查", "audio.extract": "2/15 音频提取",
    "transcript.decode": "3/15 语音识别", "transcript.normalize": "4/15 转写规范化",
    "frames.candidates": "5/15 候选画面", "frames.select": "6/15 关键画面",
    "knowledge.plan": "7/15 课程规划", "visual.jobs": "8/15 视觉任务",
    "visual.evidence": "9/15 视觉证据", "frames.semantics": "10/15 画面语义",
    "knowledge.course_ir": "11/15 课程 IR", "knowledge.units": "12/15 知识单元",
    "knowledge.selfcheck": "13/15 内容自检", "document.assemble": "14/15 文档组装",
    "render.bundle": "15/15 文档生成", "completed": "已完成",
    "cancelling": "正在取消", "cancelled": "已取消", "failed": "失败",
}


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    hours, rest = divmod(max(0, int(seconds)), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours:d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def format_eta(seconds: float | None, estimating: bool = False) -> str:
    return "估算中" if seconds is None and estimating else format_duration(seconds)


def cloud_authorization_message(qwen: dict, *, aggregate: bool) -> str:
    budget = qwen.get("budget", {})
    models = " → ".join(qwen.get("_runtime_models", []))
    endpoint = str(qwen.get("_runtime_base_url") or qwen.get("default_base_url") or "未配置")
    calls = max(0, int(budget.get("max_calls_per_video", 1)))
    chars = int(budget.get("max_input_chars", 60000))
    output = int(budget.get("max_output_tokens", 5000))
    planning = min(output, int(budget.get("planning_max_output_tokens", 3200)))
    if aggregate:
        return (
            "聚合模式会发送所选视频的缓存知识文本与来源 ID。\n"
            f"请求端点：{endpoint}\n候选模型：{models}\n本次最多 {calls} 次请求；输入不超过 {chars:,} 字符，输出不超过 {output:,} Tokens。\n"
            "不发送视频、截图或密钥。是否明确授权？"
        )
    return (
        "普通模式会发送压缩转写文本与来源块 ID。\n"
        f"请求端点：{endpoint}\n候选模型：{models}\n每个视频的规划、整理和失败回退全流程共享最多 {calls} 次请求；正常成功路径通常为规划 1 次 + 整理 1 次。\n"
        f"每次输入不超过 {chars:,} 字符；规划输出不超过 {planning:,} Tokens，整理输出不超过 {output:,} Tokens。\n"
        "不发送视频、截图或密钥。是否明确授权？"
    )


def watermark_options(config: AppConfig) -> tuple[str, float]:
    value = config.raw.get("desktop", {}).get("watermark", {})
    value = value if isinstance(value, dict) else {}
    try:
        opacity = float(value.get("opacity", 0.14))
    except (TypeError, ValueError):
        opacity = 0.14
    return str(value.get("text", "powed by Fx")).strip(), min(0.35, max(0.05, opacity))


def blended_hex(foreground: str, background: str, opacity: float) -> str:
    opacity = min(1.0, max(0.0, opacity))
    fg = tuple(int(foreground[index:index + 2], 16) for index in (1, 3, 5))
    bg = tuple(int(background[index:index + 2], 16) for index in (1, 3, 5))
    mixed = tuple(round(back + (front - back) * opacity) for front, back in zip(fg, bg))
    return "#" + "".join(f"{channel:02x}" for channel in mixed)


def launch_desktop(config: AppConfig) -> None:
    import tkinter as tk
    from functools import partial
    from ..application.processing import resolve_cloud_authorization
    from ..localplay import register_protocol
    from .controller import DesktopController
    from .view import DesktopView
    if __import__("platform").system() == "Windows":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            pass
    register_protocol(config.root / "config.yaml")
    root = tk.Tk()
    DesktopView(
        root, config, DesktopController(DefaultProcessingService(config)),
        partial(resolve_cloud_authorization, config),
    )
    root.mainloop()


__all__ = [
    "DesktopState", "QueueItem", "STAGE_LABELS", "UiEvent", "VISUAL_TEACHING_LABELS",
    "VISUAL_TEACHING_LEVELS", "blended_hex", "cloud_authorization_message", "config_with_content_level",
    "config_with_visual_teaching_level", "format_duration", "format_eta", "launch_desktop",
    "qwen_asr_ready", "save_api_credentials", "save_desktop_settings", "validate_desktop_settings",
    "validate_speech_models", "watermark_options",
]
