from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from zhiying.knowledge.editorial import DEFAULT_BRIEF_TEXT, EditorialBrief, load_brief
from zhiying.knowledge.planning import (
    _assign_depth_contracts,
    _validate_plan_payload,
    build_lesson_plan,
    collect_visual_jobs,
    plan_offline,
    validate_visual_question,
)
from zhiying.knowledge.schema import ChapterPlan, LessonPlan, UnitPlan, VisualNeed, VisualQuestion


class PlanningTests(unittest.TestCase):
    def _make_transcript(self) -> dict:
        return {
            "segments": [
                {"segment_id": "seg_00001", "start_seconds": 1.0, "end_seconds": 5.0, "text": "黑K的判定规则是看M5"},
                {"segment_id": "seg_00002", "start_seconds": 6.0, "end_seconds": 10.0, "text": "与M5同价不算黑K"},
                {"segment_id": "seg_00003", "start_seconds": 11.0, "end_seconds": 15.0, "text": "中枢的定义"},
            ]
        }

    def test_plan_offline_basic(self) -> None:
        plan = plan_offline(self._make_transcript(), "推荐")
        self.assertIsInstance(plan, LessonPlan)
        self.assertTrue(plan.chapters)
        self.assertTrue(plan.all_unit_plans)

    def test_plan_offline_detail_levels(self) -> None:
        plan = plan_offline(self._make_transcript(), "丰富")
        detail_levels = {up.detail_level for up in plan.all_unit_plans}
        self.assertTrue(detail_levels)

    def test_build_is_deterministic_without_domain_cache_io(self) -> None:
        transcript = self._make_transcript()
        p1, _ = build_lesson_plan(transcript, "推荐", {})
        p2, _ = build_lesson_plan(transcript, "推荐", {})
        self.assertEqual(p1.to_dict(), p2.to_dict())

    def test_build_offline_no_cloud(self) -> None:
        plan, info = build_lesson_plan(self._make_transcript(), "推荐", {}, cloud=False)
        self.assertIsInstance(plan, LessonPlan)
        self.assertEqual(info, {})

    def test_plan_has_terminology(self) -> None:
        plan = plan_offline(self._make_transcript(), "推荐")
        self.assertIsInstance(plan.terminology, list)

    def test_plan_has_v21_depth_contract(self) -> None:
        plan = plan_offline(self._make_transcript(), "推荐")
        unit = plan.all_unit_plans[0]
        self.assertTrue(unit.evidence_spans)
        self.assertGreater(unit.target_chars, 0)
        self.assertIn(unit.detail_level, {"mention", "brief", "standard", "deep"})
        self.assertEqual(unit.to_dict()["depth_level"], unit.detail_level)

    def test_plan_has_v22_visual_profile_and_contract(self) -> None:
        transcript = {"segments": [
            {"segment_id": "seg_00001", "start_seconds": 1.0, "end_seconds": 6.0,
             "text": "看K线和均线的走势图，定位中枢边界与重叠区间"},
            {"segment_id": "seg_00002", "start_seconds": 7.0, "end_seconds": 12.0,
             "text": "再看这根K线如何穿过均线并形成新的区间"},
        ]}
        plan = plan_offline(transcript, "推荐", "enhanced")
        self.assertEqual(plan.visual_profile.course_form, "chart_analysis")
        visual_units = [unit for unit in plan.all_unit_plans if unit.visual_need.required]
        self.assertTrue(visual_units)
        self.assertIn(visual_units[0].visual_need.role, {"locate", "explain", "evidence"})
        self.assertTrue(visual_units[0].visual_need.success_criteria)
        self.assertFalse(any("能够回答知识问题" in item for item in visual_units[0].visual_need.success_criteria))
        self.assertLessEqual(visual_units[0].visual_need.max_count, 3)

    def test_formula_case_is_visually_required_even_when_cloud_plan_omits_flag(self) -> None:
        transcript = {"segments": [{
            "segment_id": "seg_00001", "start_seconds": 0.0, "end_seconds": 20.0,
            "text": "题目是 x 的五次方除以根号一加 x 平方，下面写两种积分解法",
        }]}
        plan = LessonPlan(chapters=[ChapterPlan(unit_plans=[UnitPlan(
            plan_id="plan_001",
            title="例题：∫ x⁵ / √(1+x²) dx 的两种解法",
            knowledge_types=["case"],
            source_segment_ids=["seg_00001"],
        )])])

        _assign_depth_contracts(plan, transcript, "精简", "enhanced")
        unit = plan.all_unit_plans[0]

        self.assertTrue(unit.visual_need.required)
        self.assertTrue(unit.visual_questions)
        self.assertTrue(collect_visual_jobs(plan, 20.0, {"vlm_compare_max_candidates": 4}))

    def test_content_and_visual_levels_are_independent(self) -> None:
        transcript = self._make_transcript()
        compact = plan_offline(transcript, "精简", "balanced")
        rich = plan_offline(transcript, "丰富", "balanced")
        compact_visual = [unit.visual_need.to_dict() for unit in compact.all_unit_plans]
        rich_visual = [unit.visual_need.to_dict() for unit in rich.all_unit_plans]
        self.assertEqual(compact_visual, rich_visual)
        self.assertNotEqual(
            [unit.target_chars for unit in compact.all_unit_plans],
            [unit.target_chars for unit in rich.all_unit_plans],
        )

    def test_cloud_plan_validator_requires_real_source_blocks(self) -> None:
        valid = {
            "chapters": [{
                "title": "章节",
                "unit_plans": [{
                    "plan_id": "plan_001",
                    "title": "知识点",
                    "source_block_ids": ["block_0001"],
                }],
            }],
        }
        _validate_plan_payload(valid, {"block_0001": ["seg_00001"]})
        invalid = {
            "chapters": [{
                "title": "章节",
                "unit_plans": [{
                    "plan_id": "plan_001",
                    "title": "知识点",
                    "source_block_ids": ["block_9999"],
                }],
            }],
        }
        with self.assertRaises(ValueError):
            _validate_plan_payload(invalid, {"block_0001": ["seg_00001"]})

    def test_visual_question_validator_rejects_pronoun_and_multi_frame_contract(self) -> None:
        contract = VisualNeed(required=True, success_criteria=["可见对象"], sequence_mode="single")
        self.assertFalse(validate_visual_question(VisualQuestion(question="这个呢？"), contract).accepted)
        self.assertFalse(validate_visual_question(
            VisualQuestion(question="哪张图能展示执行前后变化过程？"), contract,
        ).accepted)

    def test_enhanced_mid_length_course_has_at_most_eight_compare_jobs(self) -> None:
        units = []
        for index in range(12):
            plan_id = f"plan_{index:03d}"
            units.append(UnitPlan(
                plan_id=plan_id,
                visual_need=VisualNeed(
                    required=True,
                    question=f"哪张图能辨认第{index}个流程节点？",
                    success_criteria=["画面中可辨认流程节点"],
                ),
                visual_questions=[VisualQuestion(
                    question_id=f"vq_{index:03d}_01",
                    unit_id=plan_id,
                    question=f"哪张图能辨认第{index}个流程节点？",
                    expected_entities=["流程节点"],
                )],
            ))
        plan = LessonPlan(chapters=[ChapterPlan(unit_plans=units)])
        jobs = collect_visual_jobs(plan, 1302.0, {"vlm_compare_max_candidates": 4})
        self.assertLessEqual(len(jobs), 8)
        self.assertTrue(all(job.max_candidates <= 4 for job in jobs))

    def test_offline_plan_has_editorial_decision(self) -> None:
        brief = load_brief(None)
        plan = plan_offline(self._make_transcript(), "推荐", brief=brief)
        self.assertTrue(plan.editorial_decision)
        self.assertEqual(plan.editorial_decision["brief_sha256"], brief.sha256)
        self.assertEqual(plan.editorial_decision["structure_mode"], "lecture_timeline")

    def test_offline_plan_without_brief_still_has_decision(self) -> None:
        plan = plan_offline(self._make_transcript(), "推荐")
        self.assertTrue(plan.editorial_decision)
        self.assertIn(plan.editorial_decision["structure_mode"], {"lecture_timeline", "hybrid"})

    def test_brief_change_invalidates_plan_cache_only(self) -> None:
        transcript = self._make_transcript()
        default_brief = load_brief(None)
        custom_brief = EditorialBrief(
            text="自定义偏好", sha256="abc123", char_count=4, is_default=False,
        )
        plan_default, _ = build_lesson_plan(transcript, "推荐", {}, brief=default_brief)
        plan_custom, _ = build_lesson_plan(transcript, "推荐", {}, brief=custom_brief)
        self.assertNotEqual(
            plan_default.editorial_decision["brief_sha256"],
            plan_custom.editorial_decision["brief_sha256"],
        )

    def test_v40_fixture_reads_without_editorial_decision(self) -> None:
        """V4.0 LessonPlan fixture 无 editorial_decision 字段仍可读取。"""
        plan = LessonPlan.from_dict({
            "schema_version": 8,
            "domain": "test",
            "course_form": "general",
            "core_thread": "主线",
            "chapters": [{"chapter_id": "ch_001", "title": "ch", "unit_plans": []}],
        })
        self.assertEqual(plan.editorial_decision, {})

    def test_plan_to_dict_includes_editorial_decision(self) -> None:
        plan = plan_offline(self._make_transcript(), "推荐")
        d = plan.to_dict()
        self.assertIn("editorial_decision", d)
        self.assertIsInstance(d["editorial_decision"], dict)


if __name__ == "__main__":
    unittest.main()
