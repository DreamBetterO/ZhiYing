from __future__ import annotations

import json
import unittest
from pathlib import Path

from video_study.document_v3 import build_document_plan, compile_document_v3, compose_chapters
from video_study.release_quality import audit_candidate_index, audit_document_v3, audit_visual_evidence


class V6GoldenSampleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        fixture = Path(__file__).parent / "fixtures" / "v6" / "golden_samples.json"
        cls.samples = json.loads(fixture.read_text(encoding="utf-8"))

    def test_math_formula_golden_preserves_equation_source_and_candidate_window(self) -> None:
        sample = self.samples["math_formula_high_math"]
        v2 = {
            "schema_version": 2,
            "metadata": {"video_id": "math", "document_title": "高数"},
            "sections": [{"title": "积分", "knowledge_points": [{
                "statement": "积分公式",
                "content_blocks": [{"block_id": "formula_1", "type": "equation", "text": "\\int f(x)dx"}],
                "source_refs": {"start_seconds": 30, "end_seconds": 45},
            }]}],
        }
        plan = build_document_plan(v2)
        audit = audit_document_v3(compile_document_v3(plan, compose_chapters(plan)))
        candidates = {"candidates": [{"timestamp_seconds": value} for value in sample["candidate_timestamps"]]}
        self.assertTrue(audit["valid"])
        self.assertGreaterEqual(audit["equation"], 1)
        self.assertGreaterEqual(audit["source_reference"], 1)
        self.assertTrue(audit_candidate_index(candidates, minimum=sample["candidate_minimum"])["valid"])

    def test_strong_and_weak_visual_goldens_are_explicit(self) -> None:
        for name in ("strong_visual", "weak_visual"):
            sample = self.samples[name]
            with self.subTest(sample=name):
                self.assertTrue(audit_visual_evidence(sample["evidence"], expected=sample["expected"])["valid"])


if __name__ == "__main__":
    unittest.main()
