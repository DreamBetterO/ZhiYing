from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import sys
from pathlib import Path

from .config import load_config
from .media import check_tools, discover_videos, probe_video
from .application.pipeline import run_all
from .runtime import add_project_tools_to_path, default_config_path


def _cloud_authorization(settings: dict):
    from .application.processing import resolve_cloud_authorization
    from .config import AppConfig
    return resolve_cloud_authorization(AppConfig(Path.cwd(), {"qwen": settings}))


def main() -> None:
    multiprocessing.freeze_support()
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    add_project_tools_to_path()
    default_config = str(default_config_path())
    parser = argparse.ArgumentParser(description="教学视频转可溯源复习文档")
    sub = parser.add_subparsers(dest="command", required=True)
    doctor = sub.add_parser("doctor", help="检查环境和输入视频")
    doctor.add_argument("--config", default=default_config)
    run = sub.add_parser("run", help="运行处理流水线")
    run.add_argument("--config", default=default_config)
    run.add_argument("--video")
    run.add_argument("--force", action="store_true")
    run.add_argument("--force-asr", action="store_true", help="只强制重跑 ASR，复用已有音频和关键帧")
    run.add_argument(
        "--force-summary", action="store_true",
        help="重算知识文档；不会隐式开启云端，请配合 --cloud-summary 明确授权",
    )
    run.add_argument("--cloud-summary", action=argparse.BooleanOptionalAction, default=None, help="明确启用或禁用云端总结")
    desktop = sub.add_parser("desktop", help="启动原生桌面软件")
    desktop.add_argument("--config", default=default_config)
    play_url = sub.add_parser("play-url", help="处理 Word/PDF 本地来源链接")
    play_url.add_argument("--config", default=default_config)
    play_url.add_argument("url")
    args = parser.parse_args()
    config = load_config(args.config)

    if args.command == "doctor":
        qwen = config.raw["qwen"]
        chain_value = os.getenv(qwen.get("model_chain_env", "QWEN_MODEL_CHAIN"), "")
        models = [item.strip() for item in chain_value.split(",") if item.strip()] or list(qwen.get("default_models", []))
        report = {"tools": check_tools(), "videos": []}
        for video in discover_videos(config.path("paths", "input_dir")):
            probe = probe_video(video)
            report["videos"].append({
                "path": str(video),
                "duration_seconds": float(probe.get("format", {}).get("duration", 0)),
                "size_bytes": video.stat().st_size,
            })
        report["model_dir"] = str(config.path("paths", "model_dir"))
        report["model_downloaded"] = config.path("paths", "model_dir").exists()
        qwen_asr_model = (config.root / config.raw.get("asr", {}).get(
            "qwen_model_dir", "models/qwen3-asr-0.6b-hf"
        )).resolve()
        qwen_asr_runtime = (config.root / config.raw.get("asr", {}).get(
            "qwen_runtime_dir", "models/qwen3-asr-runtime"
        )).resolve()
        report["local_asr"] = {
            "default": config.raw.get("asr", {}).get("engine", "faster-whisper"),
            "qwen3_asr_model": str(qwen_asr_model),
            "qwen3_asr_downloaded": qwen_asr_model.exists(),
            "qwen3_asr_runtime_ready": qwen_asr_runtime.exists(),
        }
        report["cloud"] = {
            "configured": bool(
                os.getenv(qwen.get("api_key_env", "QWEN_API_KEY"))
                and os.getenv(qwen.get("base_url_env", "QWEN_BASE_URL"), qwen.get("default_base_url"))
            ),
            "enabled": os.getenv(qwen.get("enabled_env", "CLOUD_LLM_ENABLED"), str(qwen.get("enabled", False))).lower() == "true",
            "models": models,
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif args.command == "run":
        from .infrastructure.single_instance import acquire_single_instance
        try:
            with acquire_single_instance(str(config.root)):
                qwen_settings = None
                qwen = config.raw.get("qwen", {})
                configured_default = bool(qwen.get("enabled", False))
                cloud_enabled = (
                    bool(args.cloud_summary)
                    if args.cloud_summary is not None
                    else os.getenv(qwen.get("enabled_env", "CLOUD_LLM_ENABLED"), str(configured_default)).lower() == "true"
                )
                if cloud_enabled:
                    qwen_settings = _cloud_authorization(qwen).legacy_settings(qwen)
                results = run_all(
                    config,
                    video=args.video,
                    force=args.force,
                    force_summary=args.force_summary,
                    cloud_summary=args.cloud_summary,
                    qwen_settings=qwen_settings,
                    force_asr=args.force_asr,
                )
                print(json.dumps([{key: str(value) for key, value in item.items()} for item in results], ensure_ascii=False, indent=2))
        except RuntimeError as exc:
            print(f"无法启动：{exc}", file=sys.stderr)
            sys.exit(2)
    elif args.command == "desktop":
        from .infrastructure.single_instance import acquire_single_instance
        try:
            with acquire_single_instance(str(config.root)):
                from .desktop import launch_desktop
                launch_desktop(config)
        except RuntimeError as exc:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            messagebox.showwarning("知影", str(exc))
            root.destroy()
            sys.exit(2)
    elif args.command == "play-url":
        from .infrastructure.playback import play_protocol_url
        play_protocol_url(config, args.url)


if __name__ == "__main__":
    main()
