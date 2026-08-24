from __future__ import annotations

import unittest

from zhiying.knowledge.selfcheck import run_selfcheck
from zhiying.knowledge.schema import KnowledgeUnit, LessonPlan, ChapterPlan, UnitPlan


class SelfCheckTests(unittest.TestCase):
    def _make_transcript(self) -> dict:
        return {
            "segments": [
                {"segment_id": "seg_00001", "start_seconds": 1.0, "end_seconds": 5.0, "text": "黑K的判定规则是看M5"},
                {"segment_id": "seg_00002", "start_seconds": 6.0, "end_seconds": 10.0, "text": "与M5同价不算黑K"},
                {"segment_id": "seg_00003", "start_seconds": 11.0, "end_seconds": 15.0, "text": "这是定义"},
            ]
        }

    def _make_lesson_plan(self) -> LessonPlan:
        return LessonPlan(
            chapters=[ChapterPlan(
                chapter_id="ch_001",
                title="ch",
                unit_plans=[
                    UnitPlan(plan_id="plan_001", title="黑K规则", role="core",
                             knowledge_types=["rule"], detail_level="deep",
                             source_segment_ids=["seg_00001"]),
                ],
            )],
        )

    def test_core_unit_without_source_is_error(self) -> None:
        units = [KnowledgeUnit(
            unit_id="u1", type="rule", title="测试", importance="core",
            definition_or_conclusion="没有来源的核心规则",
        )]
        report = run_selfcheck(units, self._make_lesson_plan(), self._make_transcript())
        self.assertFalse(report.passed)
        self.assertTrue(any(r.check == "source_existence" for r in report.errors))

    def test_valid_source_passes(self) -> None:
        units = [KnowledgeUnit(
            unit_id="u1", type="rule", title="黑K规则", importance="core",
            definition_or_conclusion="与M5同价不算黑K",
            evidence_refs=[{"segment_ids": ["seg_00001", "seg_00002"]}],
        )]
        report = run_selfcheck(units, self._make_lesson_plan(), self._make_transcript())
        self.assertTrue(report.passed)

    def test_direction_word_warning(self) -> None:
        units = [KnowledgeUnit(
            unit_id="u1", type="rule", title="黑K", importance="core",
            definition_or_conclusion="黑K的判定规则",
            evidence_refs=[{"segment_ids": ["seg_00002"]}],
        )]
        report = run_selfcheck(units, self._make_lesson_plan(), self._make_transcript())
        self.assertTrue(any(r.check == "direction_word" for r in report.warnings))

    def test_expand_completeness_warning(self) -> None:
        lesson_plan = LessonPlan(
            chapters=[ChapterPlan(
                chapter_id="ch_001",
                title="ch",
                unit_plans=[UnitPlan(
                    plan_id="plan_001", title="黑K规则", role="core",
                    knowledge_types=["rule"], detail_level="deep",
                    source_segment_ids=["seg_00001"],
                )],
            )],
        )
        units = [KnowledgeUnit(
            unit_id="u1", type="rule", title="黑K规则", importance="core",
            definition_or_conclusion="黑K的判定",
            evidence_refs=[{"segment_ids": ["seg_00001"]}],
            detail_level="deep",
        )]
        report = run_selfcheck(units, lesson_plan, self._make_transcript())
        self.assertTrue(any(r.check == "expand_completeness" for r in report.warnings))

    def test_conflict_detection(self) -> None:
        units = [
            KnowledgeUnit(
                unit_id="u1", type="rule", title="黑K判定", importance="core",
                definition_or_conclusion="与M5同价是黑K",
                evidence_refs=[{"segment_ids": ["seg_00001"]}],
            ),
            KnowledgeUnit(
                unit_id="u2", type="rule", title="黑K判定", importance="core",
                definition_or_conclusion="与M5同价不算黑K",
                evidence_refs=[{"segment_ids": ["seg_00002"]}],
            ),
        ]
        report = run_selfcheck(units, self._make_lesson_plan(), self._make_transcript())
        self.assertTrue(any(r.check == "conflict" for r in report.warnings))

    def test_stats_include_detail_levels(self) -> None:
        lesson_plan = LessonPlan(
            chapters=[ChapterPlan(
                chapter_id="ch_001",
                title="ch",
                unit_plans=[
                    UnitPlan(plan_id="plan_001", title="p1", role="core",
                             knowledge_types=["rule"], detail_level="deep"),
                    UnitPlan(plan_id="plan_002", title="p2", role="supporting",
                             knowledge_types=["concept"], detail_level="standard"),
                    UnitPlan(plan_id="plan_003", title="p3", role="supporting",
                             knowledge_types=["concept"], detail_level="brief"),
                ],
            )],
        )
        units = [
            KnowledgeUnit(unit_id="u1", type="rule", title="p1", importance="core",
                          detail_level="deep",
                          evidence_refs=[{"segment_ids": ["seg_00001"]}]),
            KnowledgeUnit(unit_id="u2", type="concept", title="p2", importance="supporting",
                          detail_level="standard",
                          evidence_refs=[{"segment_ids": ["seg_00001"]}]),
            KnowledgeUnit(unit_id="u3", type="concept", title="p3", importance="supporting",
                          detail_level="brief",
                          evidence_refs=[{"segment_ids": ["seg_00001"]}]),
        ]
        report = run_selfcheck(units, lesson_plan, self._make_transcript())
        self.assertEqual(report.stats["deep"], 1)
        self.assertEqual(report.stats["standard"], 1)
        self.assertEqual(report.stats["brief"], 1)

    def test_visual_evidence_without_frame(self) -> None:
        units = [KnowledgeUnit(
            unit_id="u1", type="visual_or_formula", title="K线图", importance="core",
            definition_or_conclusion="图中显示的走势",
            evidence_refs=[{"segment_ids": ["seg_00001"]}],
        )]
        frames = {"frames": [{"image_id": "f1", "timestamp_seconds": 100.0}]}
        report = run_selfcheck(units, self._make_lesson_plan(), self._make_transcript(), frames)
        self.assertTrue(any(r.check == "visual_evidence" for r in report.warnings))

    def test_report_serializable(self) -> None:
        units = [KnowledgeUnit(unit_id="u1", type="concept", title="测试", importance="core",
                               evidence_refs=[{"segment_ids": ["seg_00001"]}])]
        d = run_selfcheck(units, self._make_lesson_plan(), self._make_transcript()).to_dict()
        self.assertIn("results", d)
        self.assertIn("stats", d)
        self.assertIn("passed", d)


if __name__ == "__main__":
    unittest.main()
