from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .config import load_config
from .envfile import import_qwen_txt
from .media import check_tools, discover_videos, probe_video
from .pipeline import run_all
from .runtime import add_bundled_tools_to_path, default_config_path, is_frozen


def main() -> None:
    add_bundled_tools_to_path()
    if is_frozen() and len(sys.argv) == 1:
        sys.argv.append("desktop")
    default_config = str(default_config_path()) if is_frozen() else "config.yaml"
    parser = argparse.ArgumentParser(description="教学视频转可溯源复习文档")
    sub = parser.add_subparsers(dest="command", required=True)
    doctor = sub.add_parser("doctor", help="检查环境和输入视频")
    doctor.add_argument("--config", default=default_config)
    run = sub.add_parser("run", help="运行处理流水线")
    run.add_argument("--config", default=default_config)
    run.add_argument("--video")
    run.add_argument("--force", action="store_true")
    run.add_argument("--force-asr", action="store_true", help="只强制重跑 ASR，复用已有音频和关键帧")
    run.add_argument("--force-summary", action="store_true")
    run.add_argument("--cloud-summary", action=argparse.BooleanOptionalAction, default=None, help="明确启用或禁用云端总结")
    desktop = sub.add_parser("desktop", help="启动原生桌面软件")
    desktop.add_argument("--config", default=default_config)
    play_url = sub.add_parser("play-url", help="处理 Word/PDF 本地来源链接")
    play_url.add_argument("--config", default=default_config)
    play_url.add_argument("url")
    import_env = sub.add_parser("import-env", help="从 QwenAPI.txt 安全生成 .env")
    import_env.add_argument("--source", default="QwenAPI.txt")
    import_env.add_argument("--output", default=".env")
    import_env.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.command == "import-env":
        result = import_qwen_txt(Path(args.source).resolve(), Path(args.output).resolve(), args.force)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
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
        results = run_all(
            config,
            video=args.video,
            force=args.force,
            force_summary=args.force_summary,
            cloud_summary=args.cloud_summary,
            force_asr=args.force_asr,
        )
        print(json.dumps([{key: str(value) for key, value in item.items()} for item in results], ensure_ascii=False, indent=2))
    elif args.command == "desktop":
        from .desktop import launch_desktop
        launch_desktop(config)
    elif args.command == "play-url":
        from .localplay import play_protocol_url
        play_protocol_url(config, args.url)


if __name__ == "__main__":
    main()
