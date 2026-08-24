from __future__ import annotations

import unittest

from zhiying.knowledge.visuals import (
    build_frame_semantics,
    cluster_visual_scenes,
    cleanup_bindings,
    _nearby_transcript,
)
from zhiying.knowledge.schema import VisualBinding


class NearbyTranscriptTests(unittest.TestCase):
    def test_extracts_window(self) -> None:
        segments = [
            {"start_seconds": 0.0, "end_seconds": 10.0, "text": "开头"},
            {"start_seconds": 30.0, "end_seconds": 40.0, "text": "中间"},
            {"start_seconds": 100.0, "end_seconds": 110.0, "text": "远处"},
        ]
        result = _nearby_transcript(35.0, segments, window=15.0)
        self.assertIn("中间", result)
        self.assertNotIn("开头", result)
        self.assertNotIn("远处", result)

    def test_truncates_long_text(self) -> None:
        segments = [
            {"start_seconds": 0.0, "end_seconds": 100.0, "text": "字" * 300},
        ]
        result = _nearby_transcript(50.0, segments, max_chars=50)
        self.assertLessEqual(len(result), 51)  # 50 + …


class BuildFrameSemanticsTests(unittest.TestCase):
    def test_basic(self) -> None:
        frames = {"frames": [
            {"image_id": "f1", "timestamp_seconds": 35.0, "path": "f1.jpg"},
        ]}
        transcript = {"segments": [
            {"segment_id": "s1", "start_seconds": 30.0, "end_seconds": 40.0, "text": "讲解"},
        ]}
        semantics = build_frame_semantics(frames, transcript)
        self.assertEqual(len(semantics), 1)
        self.assertEqual(semantics[0].frame_id, "f1")
        self.assertIn("讲解", semantics[0].nearby_transcript)
        self.assertIn("nearby_transcript", semantics[0].semantic_source)
        self.assertGreater(semantics[0].confidence, 0.0)

    def test_is_deterministic_without_domain_cache_io(self) -> None:
        frames = {"frames": [
            {"image_id": "f1", "timestamp_seconds": 35.0, "path": "f1.jpg"},
        ]}
        transcript = {"segments": [
            {"segment_id": "s1", "start_seconds": 30.0, "end_seconds": 40.0, "text": "讲解"},
        ]}
        s1 = build_frame_semantics(frames, transcript)
        s2 = build_frame_semantics(frames, transcript)
        self.assertEqual([item.to_dict() for item in s1], [item.to_dict() for item in s2])


class SceneClusteringTests(unittest.TestCase):
    def test_near_duplicate_jpegs_share_scene(self) -> None:
        import tempfile
        from pathlib import Path
        from PIL import Image, ImageDraw

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            image = Image.new("RGB", (160, 90), "white")
            draw = ImageDraw.Draw(image)
            draw.rectangle((20, 15, 130, 70), outline="black", width=3)
            first = root / "candidate_00043.jpg"
            second = root / "candidate_00044.jpg"
            different = root / "candidate_00045.jpg"
            image.save(first, quality=75)
            image.save(second, quality=95)
            Image.new("RGB", (160, 90), "black").save(different)

            rows = cluster_visual_scenes([
                {"image_id": "candidate_00043", "timestamp_seconds": 607.0, "path": str(first), "content_score": 0.4},
                {"image_id": "candidate_00044", "timestamp_seconds": 622.0, "path": str(second), "content_score": 0.5},
                {"image_id": "candidate_00045", "timestamp_seconds": 635.0, "path": str(different), "content_score": 0.1},
            ])
            by_id = {row["image_id"]: row for row in rows}
            self.assertEqual(by_id["candidate_00043"]["scene_cluster_id"], by_id["candidate_00044"]["scene_cluster_id"])
            self.assertNotEqual(by_id["candidate_00043"]["scene_cluster_id"], by_id["candidate_00045"]["scene_cluster_id"])
            self.assertEqual(sum(bool(row["is_canonical"]) for row in rows[:2]), 1)


class CleanupBindingsTests(unittest.TestCase):
    def test_time_only_basis_rejected(self) -> None:
        bindings = [VisualBinding(
            frame_id="f1", unit_id="p1", confidence=0.9,
            basis=["time"], decision="bind",
        )]
        result = cleanup_bindings(bindings)
        self.assertEqual(result[0].decision, "none")

    def test_low_confidence_rejected(self) -> None:
        bindings = [VisualBinding(
            frame_id="f1", unit_id="p1", confidence=0.1,
            basis=["ocr"], decision="bind",
        )]
        result = cleanup_bindings(bindings)
        self.assertEqual(result[0].decision, "none")

    def test_same_frame_keeps_highest(self) -> None:
        bindings = [
            VisualBinding(frame_id="f1", unit_id="p1", confidence=0.5,
                          basis=["ocr"], decision="bind"),
            VisualBinding(frame_id="f1", unit_id="p2", confidence=0.9,
                          basis=["ocr"], decision="bind"),
        ]
        result = cleanup_bindings(bindings)
        bind_decisions = [b for b in result if b.decision == "bind"]
        self.assertEqual(len(bind_decisions), 1)
        self.assertEqual(bind_decisions[0].unit_id, "p2")

    def test_max_per_unit(self) -> None:
        bindings = [
            VisualBinding(frame_id="f1", unit_id="p1", confidence=0.9,
                          relation="a", basis=["ocr"], decision="bind"),
            VisualBinding(frame_id="f2", unit_id="p1", confidence=0.8,
                          relation="b", basis=["ocr"], decision="bind"),
            VisualBinding(frame_id="f3", unit_id="p1", confidence=0.7,
                          relation="c", basis=["ocr"], decision="bind"),
        ]
        result = cleanup_bindings(bindings, max_per_unit=2)
        bind_decisions = [b for b in result if b.decision == "bind"]
        self.assertEqual(len(bind_decisions), 2)

    def test_empty(self) -> None:
        self.assertEqual(cleanup_bindings([]), [])


if __name__ == "__main__":
    unittest.main()
