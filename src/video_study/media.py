from __future__ import annotations

import json
import ctypes
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .utils import now_iso, quick_fingerprint, run, run_cancellable, safe_name, write_json
from .runtime import find_tool
from .execution.artifacts import SOURCE_MANIFEST, WorkspaceLayout


_CUDA_DLL_HANDLES: list[object] = []
_CUDA_LIBRARY_HANDLES: list[object] = []


def prepare_cuda_runtime() -> list[str]:
    """登记当前 Python/Conda 环境自带的 CUDA DLL 目录。"""
    if os.name != "nt" or not hasattr(os, "add_dll_directory"):
        return []
    prefixes = [Path(sys.prefix)]
    conda_prefix = os.getenv("CONDA_PREFIX")
    if conda_prefix and Path(conda_prefix) not in prefixes:
        prefixes.append(Path(conda_prefix))
    configured = [
        Path(item.strip())
        for item in os.getenv("VIDEO_STUDY_CUDA_DLL_DIRS", "").split(os.pathsep)
        if item.strip()
    ]
    required = ("cublas64_12.dll", "cudnn64_9.dll")
    # PyTorch wheel 通常自带完整 CUDA 运行时。此时不要再混入 Conda bin，
    # 否则可能同时加载 libomp.dll 和 libiomp5md.dll，导致进程中止。
    candidates = list(configured)
    configured_complete = bool(configured) and all(
        any((directory / name).is_file() for directory in configured)
        for name in required
    )
    if not configured_complete:
        for prefix in prefixes:
            torch_lib = prefix / "Lib" / "site-packages" / "torch" / "lib"
            candidates.append(torch_lib)
            if not all((torch_lib / name).is_file() for name in required):
                candidates.append(prefix / "Library" / "bin")
    added: list[str] = []
    for directory in dict.fromkeys(candidates):
        if not directory.is_dir():
            continue
        resolved = str(directory.resolve())
        try:
            handle = os.add_dll_directory(resolved)
        except OSError:
            continue
        _CUDA_DLL_HANDLES.append(handle)
        added.append(resolved)
    if os.name == "nt":
        # CTranslate2 会按名称延迟加载这些库，显式按依赖顺序预加载。
        for name in ("cublasLt64_12.dll", "cublas64_12.dll", "cudnn64_9.dll"):
            for directory in added:
                library = Path(directory) / name
                if not library.is_file():
                    continue
                try:
                    _CUDA_LIBRARY_HANDLES.append(ctypes.WinDLL(str(library)))
                except OSError:
                    pass
                break
    return added


def discover_videos(input_dir: Path) -> list[Path]:
    if not input_dir.exists():
        sibling = next(
            (item for item in input_dir.parent.iterdir() if item.is_dir() and item.name.lower() == input_dir.name.lower()),
            None,
        )
        if sibling:
            input_dir = sibling
    return sorted(path.resolve() for path in input_dir.rglob("*") if path.is_file() and path.suffix.lower() == ".mp4")


def probe_video(video: Path) -> dict:
    result = run([
        "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(video)
    ], capture=True)
    return json.loads(result.stdout)


def create_manifest(video: Path, work_root: Path) -> tuple[Path, dict]:
    fingerprint = quick_fingerprint(video)
    video_id = f"{safe_name(video.stem, 48)}-{fingerprint}"
    layout = WorkspaceLayout(work_root, video_id)
    work_dir = layout.video_root
    manifest_path = layout.artifact_paths(SOURCE_MANIFEST)[0]
    probe = probe_video(video)
    duration = float(probe.get("format", {}).get("duration", 0.0))
    manifest = {
        "schema_version": 1,
        "video_id": video_id,
        "title": video.stem,
        "source_path": str(video.resolve()),
        "fingerprint": fingerprint,
        "duration_seconds": duration,
        "size_bytes": video.stat().st_size,
        "created_at": now_iso(),
        "probe": probe,
        "stages": {},
    }
    if manifest_path.exists():
        old = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["created_at"] = old.get("created_at", manifest["created_at"])
        manifest["stages"] = old.get("stages", {})
    write_json(manifest_path, manifest)
    return work_dir, manifest


def extract_audio(video: Path, output: Path, force: bool = False, cancel_check=None) -> Path:
    if output.exists() and not force:
        return output
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_name(output.stem + ".partial" + output.suffix)
    if temp.exists():
        temp.unlink()
    run_cancellable([
        "ffmpeg", "-hide_banner", "-loglevel", "warning", "-y",
        "-i", str(video), "-vn", "-ac", "1", "-ar", "16000",
        "-sample_fmt", "s16", "-c:a", "flac", "-compression_level", "5", str(temp),
    ], cancel_check=cancel_check)
    temp.replace(output)
    return output


def check_tools() -> dict[str, str | bool]:
    result: dict[str, str | bool] = {}
    for command in ("ffmpeg", "ffprobe", "node", "npm"):
        result[command] = find_tool(command) or False
    try:
        gpu = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
            capture_output=True, text=True, check=True, encoding="utf-8", errors="replace"
        )
        result["gpu"] = gpu.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        result["gpu"] = False
    result["asr_cuda_runtime"] = check_asr_cuda_runtime(bool(result["gpu"]))
    return result


def check_asr_cuda_runtime(gpu_visible: bool = True) -> dict[str, object]:
    """只做动态库可加载性检查，不初始化模型、不下载依赖。"""
    if not gpu_visible:
        return {"available": False, "missing": [], "reason": "未检测到 NVIDIA GPU"}
    dll_dirs = prepare_cuda_runtime()
    if os.name == "nt":
        required = ("cublas64_12.dll", "cudnn64_9.dll")
        loader = ctypes.WinDLL
    else:
        required = ("libcublas.so.12", "libcudnn.so.9")
        loader = ctypes.CDLL
    missing = []
    for library in required:
        try:
            loader(library)
        except OSError:
            missing.append(library)
    return {
        "available": not missing,
        "missing": missing,
        "reason": "" if not missing else "GPU 可见，但 faster-whisper 所需 CUDA 动态库不可加载",
        "dll_dirs": dll_dirs,
    }


class MediaAdapter:
    """把现有媒体函数暴露为 execution MediaPort，不改变算法实现。"""

    def probe(self, video: Path) -> dict:
        return probe_video(video)

    def extract_audio(self, video: Path, output: Path, *, cancel_check) -> Path:
        return extract_audio(video, output, force=True, cancel_check=cancel_check)

    def extract_frame_candidates(
        self,
        video: Path,
        output_dir: Path,
        options: dict,
        *,
        cancel_check,
    ) -> dict:
        from .frames import sample_frame_candidates
        duration = float(options.get("duration_seconds", 0.0))
        return sample_frame_candidates(
            video, output_dir, duration, dict(options), cancel_check=cancel_check,
        )
