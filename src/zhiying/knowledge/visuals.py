"""视觉语义：候选帧语义信息、附近转写、OCR provider 接口和绑定清理。

不安装 OCR/VLM 也能完成，只是更保守地少配图。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from ..utils import now_iso, write_json
from .schema import FrameSemantic, VisualBinding

_VISUALS_VERSION = 1


def _nearby_transcript(
    timestamp: float,
    segments: list[dict],
    window: float = 45.0,
    max_chars: int = 200,
) -> str:
    """提取时间戳前后 window 秒的转写文本，压缩到 max_chars。"""
    lower = timestamp - window
    upper = timestamp + window
    texts: list[str] = []
    for seg in segments:
        start = float(seg.get("start_seconds", 0))
        end = float(seg.get("end_seconds", 0))
        if end < lower or start > upper:
            continue
        text = str(seg.get("text", "")).strip()
        if text:
            texts.append(text)
    result = "；".join(texts)
    if len(result) > max_chars:
        result = result[:max_chars] + "…"
    return result


def _infer_visual_type(path: str) -> str:
    """简单推断图片类型（首轮不做复杂分类）。"""
    return "other"


def _perceptual_hash(path: str, hash_size: int = 8) -> str:
    """平均感知哈希（aHash），仅用于候选聚类。"""
    try:
        from PIL import Image
        with Image.open(path) as img:
            gray = img.convert("L").resize((hash_size, hash_size))
            pixels = gray.tobytes()
            avg = sum(pixels) / len(pixels)
            bits = "".join("1" if p > avg else "0" for p in pixels)
            return f"{int(bits, 2):0{hash_size * hash_size // 4}x}"
    except Exception:
        return ""


def _difference_hash(path: str, hash_size: int = 8) -> str:
    """差异感知哈希（dHash），补充识别版式相同但字节不同的帧。"""
    try:
        from PIL import Image
        with Image.open(path) as img:
            gray = img.convert("L").resize((hash_size + 1, hash_size))
            pixels = gray.tobytes()
            bits: list[str] = []
            for row in range(hash_size):
                offset = row * (hash_size + 1)
                bits.extend(
                    "1" if pixels[offset + col] > pixels[offset + col + 1] else "0"
                    for col in range(hash_size)
                )
            return f"{int(''.join(bits), 2):0{hash_size * hash_size // 4}x}"
    except Exception:
        return ""


def _hash_distance(left: str, right: str) -> int:
    if not left or not right or len(left) != len(right):
        return 10_000
    try:
        return (int(left, 16) ^ int(right, 16)).bit_count()
    except ValueError:
        return 10_000


def _file_sha256(path: str) -> str:
    try:
        digest = hashlib.sha256()
        with Path(path).open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return ""


def _structural_similarity(left_path: str, right_path: str, size: int = 64) -> float:
    """短邻域近似 SSIM；只在哈希已接近的少量候选间计算。"""
    try:
        from PIL import Image

        with Image.open(left_path) as left_image, Image.open(right_path) as right_image:
            left = left_image.convert("L").resize((size, size)).tobytes()
            right = right_image.convert("L").resize((size, size)).tobytes()
        count = len(left)
        if not count or count != len(right):
            return 0.0
        left_mean = sum(left) / count
        right_mean = sum(right) / count
        left_var = sum((value - left_mean) ** 2 for value in left) / count
        right_var = sum((value - right_mean) ** 2 for value in right) / count
        covariance = sum(
            (left_value - left_mean) * (right_value - right_mean)
            for left_value, right_value in zip(left, right)
        ) / count
        c1 = (0.01 * 255) ** 2
        c2 = (0.03 * 255) ** 2
        numerator = (2 * left_mean * right_mean + c1) * (2 * covariance + c2)
        denominator = (left_mean ** 2 + right_mean ** 2 + c1) * (left_var + right_var + c2)
        return max(0.0, min(1.0, numerator / denominator if denominator else 0.0))
    except Exception:
        return 0.0


def _frame_quality(row: dict) -> float:
    """canonical 帧只做轻量稳定排序，不承担语义匹配。"""
    score = float(row.get("content_score", 0.0))
    path = str(row.get("path", ""))
    try:
        from PIL import Image, ImageFilter, ImageStat

        with Image.open(path) as image:
            edge = image.convert("L").resize((160, 90)).filter(ImageFilter.FIND_EDGES)
            score += min(1.0, float(ImageStat.Stat(edge).stddev[0]) / 48.0) * 0.35
    except Exception:
        pass
    return round(score, 4)


def cluster_visual_scenes(
    frames: list[dict],
    max_neighbor_seconds: float = 45.0,
    max_scene_span_seconds: float = 150.0,
    hash_threshold: int = 6,
    ssim_threshold: float = 0.97,
) -> list[dict]:
    """跨问题建立课程级近重复场景，并标记每组 canonical frame。"""
    rows = [dict(frame) for frame in frames]
    if not rows:
        return []
    rows.sort(key=lambda row: (float(row.get("timestamp_seconds", 0.0)), str(row.get("image_id", ""))))
    parents = list(range(len(rows)))
    group_starts = [float(row.get("timestamp_seconds", 0.0)) for row in rows]
    group_ends = list(group_starts)

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int, enforce_span: bool = True) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            combined_start = min(group_starts[left_root], group_starts[right_root])
            combined_end = max(group_ends[left_root], group_ends[right_root])
            if enforce_span and combined_end - combined_start > max_scene_span_seconds:
                return
            parents[right_root] = left_root
            group_starts[left_root] = combined_start
            group_ends[left_root] = combined_end

    exact_hash_owner: dict[str, int] = {}
    for index, row in enumerate(rows):
        path = str(row.get("path", ""))
        row["image_sha256"] = _file_sha256(path)
        row["perceptual_hash"] = _perceptual_hash(path)
        row["difference_hash"] = _difference_hash(path)
        row["canonical_quality"] = _frame_quality(row)
        if row["image_sha256"]:
            owner = exact_hash_owner.setdefault(row["image_sha256"], index)
            union(owner, index, enforce_span=False)

    for left_index, left in enumerate(rows):
        left_time = float(left.get("timestamp_seconds", 0.0))
        for right_index in range(left_index + 1, len(rows)):
            right = rows[right_index]
            right_time = float(right.get("timestamp_seconds", 0.0))
            if right_time - left_time > max_neighbor_seconds:
                break
            ahash_distance = _hash_distance(left["perceptual_hash"], right["perceptual_hash"])
            dhash_distance = _hash_distance(left["difference_hash"], right["difference_hash"])
            if ahash_distance > hash_threshold or dhash_distance > hash_threshold + 2:
                continue
            similarity = _structural_similarity(str(left.get("path", "")), str(right.get("path", "")))
            if similarity >= ssim_threshold:
                union(left_index, right_index)

    groups: dict[int, list[int]] = {}
    for index in range(len(rows)):
        groups.setdefault(find(index), []).append(index)

    ordered_groups = sorted(groups.values(), key=lambda indexes: indexes[0])
    for group_number, indexes in enumerate(ordered_groups, start=1):
        cluster_id = f"scene_{group_number:05d}"
        canonical_index = max(
            indexes,
            key=lambda index: (float(rows[index].get("canonical_quality", 0.0)), -index),
        )
        for index in indexes:
            rows[index]["scene_cluster_id"] = cluster_id
            rows[index]["dedup_group_id"] = cluster_id
            rows[index]["cluster_size"] = len(indexes)
            rows[index]["canonical_frame_id"] = str(rows[canonical_index].get("image_id", ""))
            rows[index]["is_canonical"] = index == canonical_index
    return rows


def dedup_similar_frames(
    frames: list[dict],
    threshold: int = 5,
) -> list[dict]:
    """用感知哈希去重相似画面，返回去重后的帧列表。"""
    if len(frames) <= 1:
        return list(frames)
    seen_hashes: list[tuple[str, dict]] = []
    result: list[dict] = []
    for frame in frames:
        path = frame.get("path", "")
        if not path:
            result.append(frame)
            continue
        h = _perceptual_hash(path)
        if not h:
            result.append(frame)
            continue
        is_dup = False
        for existing_hash, _ in seen_hashes:
            diff = sum(c1 != c2 for c1, c2 in zip(h, existing_hash))
            if diff < threshold:
                is_dup = True
                break
        if not is_dup:
            seen_hashes.append((h, frame))
            result.append(frame)
    return result


def build_frame_semantics(
    frames: dict,
    transcript: dict,
    ocr_provider: Callable[[str], str] | None = None,
) -> list[FrameSemantic]:
    """为每个候选帧生成语义信息。

    - nearby_transcript: 前后 45s 转写拼接并压缩
    - ocr_text: 可选 OCR，不可用时为空
    - confidence: 基于 semantic_source 数量
    """
    frame_rows = frames.get("frames", [])
    segments = transcript.get("segments", [])

    semantics: list[FrameSemantic] = []
    for frame in frame_rows:
        frame_id = str(frame.get("image_id", ""))
        timestamp = float(frame.get("timestamp_seconds", 0.0))
        path = str(frame.get("path", ""))

        nearby = _nearby_transcript(timestamp, segments)
        ocr_text = ""
        if ocr_provider and path:
            try:
                ocr_text = ocr_provider(path)
            except Exception:
                ocr_text = ""

        semantic_source: list[str] = []
        if nearby:
            semantic_source.append("nearby_transcript")
        if ocr_text:
            semantic_source.append("ocr")

        confidence = min(1.0, len(semantic_source) * 0.4)
        if ocr_text:
            confidence = min(1.0, confidence + 0.2)

        semantics.append(FrameSemantic(
            frame_id=frame_id,
            timestamp_seconds=timestamp,
            path=path,
            ocr_text=ocr_text,
            nearby_transcript=nearby,
            visual_description="",
            visual_type=_infer_visual_type(path),
            semantic_source=semantic_source,
            confidence=confidence,
        ))

    return semantics


def cleanup_bindings(
    raw_bindings: list[dict[str, Any]] | list[VisualBinding],
    max_per_unit: int = 2,
    min_confidence: float = 0.3,
) -> list[VisualBinding]:
    """本地去重和容量限制。

    - 同图多绑定：保留最高置信度
    - 单点超两图：保留关系不同且置信度最高的两张
    - 拒绝 basis 只有 time 的绑定
    - 低于阈值的绑定 decision 设为 none
    """
    # 统一转为 VisualBinding
    bindings: list[VisualBinding] = []
    for item in raw_bindings:
        if isinstance(item, VisualBinding):
            bindings.append(item)
        elif isinstance(item, dict):
            bindings.append(VisualBinding.from_dict(item))

    if not bindings:
        return []

    # 拒绝 basis 只有 time 的绑定
    for b in bindings:
        if b.basis == ["time"] or (len(b.basis) == 1 and b.basis[0] == "time"):
            b.decision = "none"

    # 低于阈值的绑定 decision 设为 none
    for b in bindings:
        if b.confidence < min_confidence:
            b.decision = "none"

    # 同图多绑定：保留最高置信度的 bind
    frame_best: dict[str, VisualBinding] = {}
    for b in bindings:
        if b.decision != "bind":
            continue
        existing = frame_best.get(b.frame_id)
        if existing is None or b.confidence > existing.confidence:
            frame_best[b.frame_id] = b
    # 非最高置信度的同图绑定设为 none
    for b in bindings:
        if b.decision == "bind" and frame_best.get(b.frame_id) is not b:
            b.decision = "none"

    # 单点超两图：保留关系不同且置信度最高的两张
    unit_bindings: dict[str, list[VisualBinding]] = {}
    for b in bindings:
        if b.decision == "bind":
            unit_bindings.setdefault(b.unit_id, []).append(b)
    for unit_id, unit_binds in unit_bindings.items():
        if len(unit_binds) <= max_per_unit:
            continue
        # 按关系分组，每组保留最高置信度
        by_relation: dict[str, VisualBinding] = {}
        for b in unit_binds:
            existing = by_relation.get(b.relation)
            if existing is None or b.confidence > existing.confidence:
                by_relation[b.relation] = b
        kept = sorted(by_relation.values(), key=lambda b: b.confidence, reverse=True)[:max_per_unit]
        kept_ids = {id(b) for b in kept}
        for b in unit_binds:
            if id(b) not in kept_ids:
                b.decision = "none"

    return bindings
