from __future__ import annotations

import json
import shutil
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

from .utils import ensure_not_cancelled, hhmmss, now_iso, run_cancellable, write_json
from .runtime import find_tool

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


def extract_keyframes(video: Path, output_dir: Path, duration: float, settings: dict, force: bool = False,
                      cancel_check=None) -> dict:
    ensure_not_cancelled(cancel_check)
    index_path = output_dir / "keyframes.json"
    configured = float(settings.get("sample_interval_seconds", 10))
    max_candidates = int(settings.get("max_candidates", 600))
    interval = max(configured, duration / max_candidates) if duration else configured
    expected_selection = {
        "version": _SELECTION_VERSION,
        "scene_change_threshold": float(settings.get("scene_change_threshold", 0.025)),
        "min_content_entropy": float(settings.get("min_content_entropy", 2.4)),
        "max_keyframes": int(settings.get("max_keyframes", 8)),
        "content_start_seconds": round(float(settings.get("content_start_seconds", 0.0)), 3),
        "min_keyframe_gap_seconds": round(float(settings.get("min_keyframe_gap_seconds", 0.0)), 3),
    }
    cached = None
    if index_path.exists() and not force:
        cached = json.loads(index_path.read_text(encoding="utf-8"))
        paths_exist = all(Path(row.get("path", "")).is_file() for row in cached.get("frames", []))
        if cached.get("selection") == expected_selection and paths_exist:
            return cached
        print("[frames] 关键帧选择逻辑或配置已更新，复用候选帧重新筛选")
    candidates = output_dir / "candidates"
    selected_dir = output_dir / "selected"
    if force:
        shutil.rmtree(candidates, ignore_errors=True)
    candidates.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(selected_dir, ignore_errors=True)
    selected_dir.mkdir(parents=True, exist_ok=True)

    width = int(settings.get("max_width", 1280))
    pattern = candidates / "candidate_%05d.jpg"
    reuse_candidates = (
        not force
        and cached is not None
        and abs(float(cached.get("sample_interval_seconds", -1)) - interval) < 0.001
        and any(candidates.glob("candidate_*.jpg"))
    )
    if not reuse_candidates:
        run_cancellable([
            find_tool("ffmpeg") or "ffmpeg", "-hide_banner", "-loglevel", "warning", "-y", "-i", str(video),
            "-vf", f"fps=1/{interval:.6f},scale={width}:-2:force_original_aspect_ratio=decrease",
            "-q:v", "2", str(pattern),
        ], cancel_check=cancel_check)

    selector_settings = dict(settings)
    selector_settings["_cancel_check"] = cancel_check
    content_start = max(0.0, float(settings.get("content_start_seconds", 0.0)))
    selector_settings["_min_candidate_index"] = int(content_start / interval)
    selector_settings["_min_candidate_gap"] = int(
        max(0.0, float(settings.get("min_keyframe_gap_seconds", 0.0))) / interval
    )
    chosen = select_keyframe_candidates(sorted(candidates.glob("candidate_*.jpg")), selector_settings)
    if not chosen:
        raise RuntimeError("没有从视频中提取到关键帧")

    rows = []
    for output_index, (source, source_index, entropy) in enumerate(chosen, start=1):
        ensure_not_cancelled(cancel_check)
        timestamp = min(duration, source_index * interval) if duration else source_index * interval
        destination = selected_dir / f"frame_{output_index:03d}.jpg"
        shutil.copy2(source, destination)
        rows.append({
            "image_id": f"frame_{output_index:03d}",
            "timestamp_seconds": round(timestamp, 3),
            "timestamp_label": hhmmss(timestamp),
            "path": str(destination.resolve()),
            "caption": f"视频关键画面（{hhmmss(timestamp)}）",
            "content_entropy": round(entropy, 4),
        })
    data = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "sample_interval_seconds": round(interval, 3),
        "selection": expected_selection,
        "frames": rows,
    }
    write_json(index_path, data)
    return data
