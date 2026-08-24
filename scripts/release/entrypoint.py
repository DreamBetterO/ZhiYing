from __future__ import annotations

import os
import sys
from pathlib import Path


def _prepend_path(entries: list[Path]) -> None:
    existing = [item for item in os.environ.get("PATH", "").split(os.pathsep) if item]
    values = [str(item) for item in entries if item.is_dir()]
    seen = {item.casefold() for item in values}
    values.extend(item for item in existing if item.casefold() not in seen)
    os.environ["PATH"] = os.pathsep.join(values)


def prepare_runtime() -> None:
    if not getattr(sys, "frozen", False):
        return
    root = Path(sys.executable).resolve().parent
    runtime = root / "models" / "qwen3-asr-runtime"
    os.environ.update(
        {
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "CLOUD_LLM_ENABLED": "false",
            "ZHIYING_CUDA_DLL_DIRS": str(runtime / "torch" / "lib"),
        }
    )
    sys.dont_write_bytecode = True
    _prepend_path(
        [
            root / "tools" / "ffmpeg",
            root / "tools" / "node",
            root / "tools" / "yt-dlp",
            runtime,
            runtime / "Library" / "bin",
        ]
    )
    if Path(sys.executable).stem.casefold() == "zhiying" and len(sys.argv) == 1:
        sys.argv.append("desktop")


def run() -> None:
    prepare_runtime()
    from zhiying.cli import main

    main()


if __name__ == "__main__":
    run()
