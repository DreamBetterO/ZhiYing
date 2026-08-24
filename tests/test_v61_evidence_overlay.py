"""CP61-1 责任测试：EvidenceCorrectionOverlay v1（不可变 transcript 上的纠错覆盖层）。"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from zhiying.editorial.evidence import (
    EvidenceCorrection,
    EvidenceCorrectionOverlay,
    detect_local_corrections,
    transcript_digest,
)

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "v61"


def _math_transcript() -> dict:
    return {
        "schema_version": 1,
        "segments": [
            {"segment_id": "seg_00001", "start_seconds": 0.0, "end_seconds": 5.0,
             "text": "如果有一个大F，它的导数等于小F，那这个大F就叫做小F的一个圆寒数"},
            {"segment_id": "seg_00002", "start_seconds": 5.0, "end_seconds": 15.0,
             "text": "小F加上任意长数，也还是小F的圆寒数"},
            {"segment_id": "seg_00003", "start_seconds": 15.0, "end_seconds": 25.0,
             "text": "如果小F只有第一类阶段，就肯定没有圆寒数"},
        ],
    }


class EvidenceOverlayTests(unittest.TestCase):
    def test_detect_high_confidence_terminology_corrections(self) -> None:
        transcript = _math_transcript()
        corrections = detect_local_corrections(transcript)
        row = next(item for item in corrections if item.raw_text == "圆寒数" and item.source_span == "seg_00001")
        self.assertEqual(row.candidate_text, "原函数")
        self.assertEqual(row.state, "accepted")
        self.assertGreaterEqual(row.confidence, 0.9)
        self.assertEqual(row.method, "local_terminology_rule")
        self.assertEqual(row.source_span, "seg_00001")

    def test_low_confidence_context_rule_is_unresolved(self) -> None:
        # “阶段”只在“第一类阶段/第二类阶段”上下文才改写为“间断”，泛化“阶段”保留
        corrections = detect_local_corrections(_math_transcript())
        self.assertTrue(all(row.state in {"accepted", "rejected", "unresolved"} for row in corrections))
        stages = [row for row in corrections if row.raw_text == "阶段"]
        self.assertTrue(stages)
        self.assertEqual(stages[0].state, "unresolved")

    def test_apply_to_replaces_only_accepted(self) -> None:
        transcript = _math_transcript()
        overlay = EvidenceCorrectionOverlay(
            version=1,
            transcript_digest=transcript_digest(transcript),
            corrections=detect_local_corrections(transcript),
        )
        text = "圆寒数 长数 阶段"
        effective = overlay.apply_to(text)
        self.assertEqual(effective, "原函数 常数 阶段")  # 阶段未改写（unresolved）
        raw = transcript["segments"][0]["text"]
        self.assertIn("圆寒数", raw)
        self.assertNotIn("原函数", raw)  # 原始 transcript 不可变

    def test_transcript_digest_is_stable_and_matches_fixture_immutability(self) -> None:
        first = _math_transcript()
        second = _math_transcript()
        self.assertEqual(transcript_digest(first), transcript_digest(second))
        self.assertEqual(len(transcript_digest(first)), 64)

    def test_overlay_does_not_mutate_transcript(self) -> None:
        transcript = _math_transcript()
        before = json.dumps(transcript, ensure_ascii=False, sort_keys=True)
        overlay = EvidenceCorrectionOverlay(
            version=1, transcript_digest=transcript_digest(transcript),
            corrections=detect_local_corrections(transcript),
        )
        overlay.apply_to(transcript["segments"][0]["text"])
        after = json.dumps(transcript, ensure_ascii=False, sort_keys=True)
        self.assertEqual(before, after)

    def test_roundtrip_serialization(self) -> None:
        transcript = _math_transcript()
        overlay = EvidenceCorrectionOverlay(
            version=1, transcript_digest=transcript_digest(transcript),
            corrections=detect_local_corrections(transcript),
        )
        restored = EvidenceCorrectionOverlay.from_dict(overlay.to_dict())
        self.assertEqual(restored.to_dict(), overlay.to_dict())
        self.assertEqual(restored.version, 1)

    def test_correction_carries_all_evidence_fields(self) -> None:
        row = detect_local_corrections(_math_transcript())[0]
        for field in ("raw_text", "candidate_text", "source_span", "evidence_refs", "confidence", "method"):
            self.assertIn(field, row.to_dict())

    def test_math_concept_fixture_known_errors_are_all_detected(self) -> None:
        fixture = json.loads((FIXTURES / "math_concept.json").read_text(encoding="utf-8"))
        transcript = fixture["transcript"]
        corrections = detect_local_corrections(transcript)
        raw_set = {row.raw_text for row in corrections}
        for error in fixture["known_asr_errors"]:
            raw = error.split("→")[0]
            self.assertIn(raw, raw_set, error)


if __name__ == "__main__":
    unittest.main()
