from __future__ import annotations

import json
import shutil
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

from ..utils import ensure_not_cancelled, hhmmss, now_iso, run_cancellable, write_json
from ..runtime import find_tool

_SELECTION_VERSION = 4


def _content_thumbnail(path: Path) -> Image.Image:
    """忽略播放器/课件软件边缘，把比较重点放到中央教学内容。"""
    with Image.open(path) as image:
        gray = image.convert("L")
        width, height = gray.size
        crop = gray.crop((int(width * 0.10), int(height * 0.14), int(width * 0.90), int(height * 0.85)))
        return crop.resize((128, 72))


def _pixel_distance(left: Image.Image, right: Image.Image) -> float:
    return ImageStat.Stat(ImageChops.difference(left, right)).mean[0] / 255.0


def _entropy(image: Image.Image) -> float:
    histogram = image.histogram()
    total = sum(histogram)
    if not total:
        return 0.0
    import math
    return -sum((count / total) * math.log2(count / total) for count in histogram if count)


def select_keyframe_candidates(paths: list[Path], settings: dict) -> list[tuple[Path, int, float]]:
    """按场景聚类后挑内容量较高的代表帧，避免只截开头和空白标题页。"""
    if not paths:
        return []
    scene_threshold = float(settings.get("scene_change_threshold", 0.025))
    min_entropy = float(settings.get("min_content_entropy", 2.4))
    max_keyframes = int(settings.get("max_keyframes", 8))
    min_candidate_index = max(0, int(settings.get("_min_candidate_index", 0)))
    min_candidate_gap = max(0, int(settings.get("_min_candidate_gap", 0)))
    # 短视频可能短于开头留白配置；此时应回退到实际可用时间轴，
    # 而不是让后续代表帧选择对空列表调用 max()。
    if min_candidate_index >= len(paths):
        min_candidate_index = 0
    scenes: list[list[tuple[Path, int, float, Image.Image]]] = []
    previous: Image.Image | None = None
    for index, path in enumerate(paths):
        ensure_not_cancelled(settings.get("_cancel_check"))
        if index < min_candidate_index:
            continue
        thumbnail = _content_thumbnail(path)
        row = (path, index, _entropy(thumbnail), thumbnail)
        if previous is None or _pixel_distance(previous, thumbnail) >= scene_threshold:
            scenes.append([row])
        else:
            scenes[-1].append(row)
        previous = thumbnail

    representatives = [max(scene, key=lambda row: row[2]) for scene in scenes]
    eligible = [row for row in representatives if row[2] >= min_entropy]
    if not eligible:
        eligible = [max(representatives, key=lambda row: row[2])]
    if len(eligible) > max_keyframes:
        # 长视频不能只按熵取全局前几名，否则桌面/弹窗等高熵过渡画面会挤掉中段课件。
        # 将有效时间轴等分，每段选一张内容量最高的代表帧，再用剩余高分帧补空缺。
        first_index = min(row[1] for row in eligible)
        last_index = max(row[1] for row in eligible)
        span = max(1.0, last_index - first_index + 1.0)
        distributed = []
        for bin_index in range(max_keyframes):
            lower = first_index + span * bin_index / max_keyframes
            upper = first_index + span * (bin_index + 1) / max_keyframes
            rows = [
                row for row in eligible
                if lower <= row[1] < upper or (bin_index == max_keyframes - 1 and row[1] == last_index)
            ]
            for row in sorted(rows, key=lambda item: item[2], reverse=True):
                if all(abs(row[1] - chosen[1]) >= min_candidate_gap for chosen in distributed):
                    distributed.append(row)
                    break
        selected_paths = {row[0] for row in distributed}
        if len(distributed) < max_keyframes:
            for row in sorted(eligible, key=lambda row: row[2], reverse=True):
                if row[0] in selected_paths:
                    continue
                if any(abs(row[1] - chosen[1]) < min_candidate_gap for chosen in distributed):
                    continue
                distributed.append(row)
                selected_paths.add(row[0])
                if len(distributed) >= max_keyframes:
                    break
        eligible = distributed[:max_keyframes]
    return [(path, index, entropy) for path, index, entropy, _ in sorted(eligible, key=lambda row: row[1])]


def candidate_sampling_parameters(duration: float, settings: dict) -> dict:
    configured = max(0.001, float(settings.get("sample_interval_seconds", 10)))
    max_candidates = max(1, int(settings.get("max_candidates", 600)))
    interval = max(configured, duration / max_candidates) if duration else configured
    return {
        "version": 1,
        "sample_interval_seconds": round(interval, 6),
        "max_candidates": max_candidates,
        "max_width": int(settings.get("max_width", 1280)),
    }


def sample_frame_candidates(
    video: Path,
    output_dir: Path,
    duration: float,
    settings: dict,
    *,
    cancel_check=None,
) -> dict:
    """仅执行 ffmpeg 采样并写候选索引；缓存与关键帧选择均由 Runner 负责。"""
    ensure_not_cancelled(cancel_check)
    sampling = candidate_sampling_parameters(duration, settings)
    candidates_dir = output_dir / "candidates"
    shutil.rmtree(candidates_dir, ignore_errors=True)
    candidates_dir.mkdir(parents=True, exist_ok=True)
    pattern = candidates_dir / "candidate_%05d.jpg"
    run_cancellable([
        find_tool("ffmpeg") or "ffmpeg", "-hide_banner", "-loglevel", "warning", "-y", "-i", str(video),
        "-vf", (
            f"fps=1/{sampling['sample_interval_seconds']:.6f},"
            f"scale={sampling['max_width']}:-2:force_original_aspect_ratio=decrease"
        ),
        "-q:v", "2", str(pattern),
    ], cancel_check=cancel_check)
    paths = sorted(candidates_dir.glob("candidate_*.jpg"))
    if not paths:
        raise RuntimeError("没有从视频中提取到候选帧")
    interval = float(sampling["sample_interval_seconds"])
    rows = [
        {
            "candidate_id": f"candidate_{index:05d}",
            "index": index - 1,
            "timestamp_seconds": round(min(duration, (index - 1) * interval), 3),
            "file": path.name,
        }
        for index, path in enumerate(paths, start=1)
    ]
    data = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "sampling": sampling,
        "candidates": rows,
    }
    write_json(output_dir / "candidates.json", data)
    return data


def select_sampled_frames(
    candidates_index: Path,
    output_dir: Path,
    duration: float,
    settings: dict,
    *,
    final_selected_dir: Path | None = None,
    cancel_check=None,
) -> dict:
    """从已采样图片纯选择并复制结果；不会调用 ffmpeg 或读取缓存记录。"""
    ensure_not_cancelled(cancel_check)
    candidate_data = json.loads(candidates_index.read_text(encoding="utf-8"))
    sampling = dict(candidate_data.get("sampling", {}))
    interval = max(0.001, float(sampling.get("sample_interval_seconds", 10.0)))
    candidates_dir = candidates_index.parent / "candidates"
    paths = [candidates_dir / str(row.get("file", "")) for row in candidate_data.get("candidates", [])]
    if not paths or not all(path.is_file() for path in paths):
        raise RuntimeError("候选帧索引引用的图片不完整")
    selector_settings = dict(settings)
    selector_settings["_cancel_check"] = cancel_check
    content_start = max(0.0, float(settings.get("content_start_seconds", 0.0)))
    selector_settings["_min_candidate_index"] = int(content_start / interval)
    selector_settings["_min_candidate_gap"] = int(
        max(0.0, float(settings.get("min_keyframe_gap_seconds", 0.0))) / interval
    )
    chosen = select_keyframe_candidates(paths, selector_settings)
    if not chosen:
        raise RuntimeError("没有从视频中提取到关键帧")
    selected_dir = output_dir / "selected"
    shutil.rmtree(selected_dir, ignore_errors=True)
    selected_dir.mkdir(parents=True, exist_ok=True)
    persisted_dir = (final_selected_dir or selected_dir).resolve()
    rows = []
    for output_index, (source, source_index, entropy) in enumerate(chosen, start=1):
        ensure_not_cancelled(cancel_check)
        timestamp = min(duration, source_index * interval) if duration else source_index * interval
        filename = f"frame_{output_index:03d}.jpg"
        shutil.copy2(source, selected_dir / filename)
        rows.append({
            "image_id": f"frame_{output_index:03d}",
            "timestamp_seconds": round(timestamp, 3),
            "timestamp_label": hhmmss(timestamp),
            "path": str(persisted_dir / filename),
            "caption": f"视频关键画面（{hhmmss(timestamp)}）",
            "content_entropy": round(entropy, 4),
        })
    selection = {
        "version": _SELECTION_VERSION,
        "scene_change_threshold": float(settings.get("scene_change_threshold", 0.025)),
        "min_content_entropy": float(settings.get("min_content_entropy", 2.4)),
        "max_keyframes": int(settings.get("max_keyframes", 8)),
        "content_start_seconds": round(content_start, 3),
        "min_keyframe_gap_seconds": round(float(settings.get("min_keyframe_gap_seconds", 0.0)), 3),
    }
    data = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "sample_interval_seconds": round(interval, 3),
        "selection": selection,
        "frames": rows,
    }
    write_json(output_dir / "keyframes.json", data)
    return data


def extract_keyframes(video: Path, output_dir: Path, duration: float, settings: dict, force: bool = False,
                      cancel_check=None) -> dict:
    # 兼容入口保留公开签名，但不再拥有任何缓存判定或复用逻辑。
    sample_frame_candidates(video, output_dir, duration, settings, cancel_check=cancel_check)
    data = select_sampled_frames(
        output_dir / "candidates.json", output_dir, duration, settings,
        final_selected_dir=output_dir / "selected", cancel_check=cancel_check,
    )
    return {**data, "_runtime": {"cache_hit": False}}
