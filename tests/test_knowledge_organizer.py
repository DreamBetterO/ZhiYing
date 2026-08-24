from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from zhiying.knowledge.organizer import _validate_organizer_payload, build_units, organize_offline
from zhiying.knowledge.prompts import compose_course_ir_prompt
from zhiying.knowledge.schema import (
    ChapterPlan,
    KnowledgeUnit,
    LessonPlan,
    UnitPlan,
    VisualNeed,
)


class OrganizerTests(unittest.TestCase):
    def test_writer_prompt_requires_formula_equivalence_and_factor_integrity(self) -> None:
        prompt = compose_course_ir_prompt(
            payload_json='{"sources":[],"units":[],"claims":[],"visuals":[]}',
            content_level="精简",
            max_tokens=5000,
        )
        self.assertIn("常数因子", prompt)
        self.assertIn("来源不足", prompt)
        self.assertIn("不得补造精确等式", prompt)

    def _make_lesson_plan(self) -> LessonPlan:
        return LessonPlan(
            domain="金融技术分析",
            course_form="rule_teaching",
            core_thread="黑K判定规则",
            chapters=[
                ChapterPlan(
                    chapter_id="chapter_001",
                    title="章节1",
                    source_segment_ids=["seg_00001", "seg_00002"],
                    unit_plans=[
                        UnitPlan(
                            plan_id="plan_001",
                            title="黑K判定规则",
                            role="core",
                            knowledge_types=["rule"],
                            detail_level="deep",
                            source_segment_ids=["seg_00001", "seg_00002"],
                        ),
                        UnitPlan(
                            plan_id="plan_002",
                            title="中枢定义",
                            role="supporting",
                            knowledge_types=["concept"],
                            detail_level="standard",
                            source_segment_ids=["seg_00003"],
                        ),
                        UnitPlan(
                            plan_id="plan_003",
                            title="跳空缺口例外",
                            role="core",
                            knowledge_types=["boundary_case"],
                            detail_level="deep",
                            source_segment_ids=["seg_00004"],
                        ),
                    ],
                ),
            ],
        )

    def _make_transcript(self) -> dict:
        return {
            "segments": [
                {"segment_id": "seg_00001", "start_seconds": 1.0, "end_seconds": 5.0, "text": "黑K的判定规则是"},
                {"segment_id": "seg_00002", "start_seconds": 6.0, "end_seconds": 10.0, "text": "与M5同价不算黑K"},
                {"segment_id": "seg_00003", "start_seconds": 11.0, "end_seconds": 15.0, "text": "中枢的定义"},
                {"segment_id": "seg_00004", "start_seconds": 16.0, "end_seconds": 20.0, "text": "跳空缺口是例外情况"},
                {"segment_id": "seg_00005", "start_seconds": 21.0, "end_seconds": 25.0, "text": "同学们好"},
            ]
        }

    def test_offline_consumes_unit_plans(self) -> None:
        lesson_plan = self._make_lesson_plan()
        transcript = self._make_transcript()
        units = organize_offline(lesson_plan, transcript, "推荐")
        # 每个 unit_plan 产生一个 unit
        self.assertEqual(len(units), 3)

    def test_offline_assigns_correct_types(self) -> None:
        lesson_plan = self._make_lesson_plan()
        transcript = self._make_transcript()
        units = organize_offline(lesson_plan, transcript, "推荐")
        types = {u.type for u in units}
        self.assertIn("rule", types)
        self.assertIn("concept", types)
        self.assertIn("boundary_case", types)

    def test_offline_preserves_plan_id_and_detail(self) -> None:
        lesson_plan = self._make_lesson_plan()
        transcript = self._make_transcript()
        units = organize_offline(lesson_plan, transcript, "推荐")
        plan_ids = {u.plan_id for u in units}
        self.assertIn("plan_001", plan_ids)
        self.assertIn("plan_002", plan_ids)
        detail_levels = {u.detail_level for u in units}
        self.assertIn("deep", detail_levels)
        self.assertIn("standard", detail_levels)

    def test_offline_evidence_refs_have_segments(self) -> None:
        lesson_plan = self._make_lesson_plan()
        transcript = self._make_transcript()
        units = organize_offline(lesson_plan, transcript, "推荐")
        for unit in units:
            self.assertTrue(unit.evidence_refs)
            self.assertTrue(unit.evidence_refs[0].get("segment_ids"))

    def test_build_units_is_deterministic_without_domain_cache_io(self) -> None:
        lesson_plan = self._make_lesson_plan()
        transcript = self._make_transcript()
        u1, _ = build_units(lesson_plan, transcript, "推荐", {})
        u2, _ = build_units(lesson_plan, transcript, "推荐", {})
        self.assertEqual([item.to_dict() for item in u1], [item.to_dict() for item in u2])

    def test_build_units_offline_no_cloud(self) -> None:
        lesson_plan = self._make_lesson_plan()
        transcript = self._make_transcript()
        units, info = build_units(lesson_plan, transcript, "推荐", {}, cloud=False)
        self.assertIsInstance(units, list)
        self.assertGreater(len(units), 0)
        self.assertEqual(info, {})

    def test_no_empty_fields_generated_from_nothing(self) -> None:
        """不得为了填满 schema 而生成来源没有的内容。"""
        lesson_plan = LessonPlan(
            chapters=[ChapterPlan(
                chapter_id="ch_001",
                title="ch",
                unit_plans=[UnitPlan(
                    plan_id="plan_001",
                    title="定义",
                    role="core",
                    knowledge_types=["concept"],
                    detail_level="standard",
                    source_segment_ids=["seg_00001"],
                )],
            )],
        )
        transcript = {"segments": [
            {"segment_id": "seg_00001", "start_seconds": 0.0, "end_seconds": 5.0, "text": "定义"}
        ]}
        units = organize_offline(lesson_plan, transcript, "推荐")
        self.assertEqual(len(units), 1)
        self.assertEqual(units[0].rules, [])
        self.assertEqual(units[0].procedure, [])
        self.assertEqual(units[0].exceptions, [])

    def test_cloud_organizer_validator_requires_exact_plan_coverage(self) -> None:
        point = {
            "plan_id": "plan_001",
            "statement": "规则",
            "explanation": "规则的来源内解释",
            "source_block_ids": ["block_0001"],
            "content_blocks": [{"block_id": "content_001", "type": "paragraph", "text": "正文"}],
        }
        payload = {"sections": [{"title": "章节", "knowledge_points": [point]}]}
        _validate_organizer_payload(payload, {"plan_001"}, {"block_0001": ["seg_00001"]})
        with self.assertRaises(ValueError):
            _validate_organizer_payload(
                payload,
                {"plan_001", "plan_002"},
                {"block_0001": ["seg_00001"]},
            )


if __name__ == "__main__":
    unittest.main()
