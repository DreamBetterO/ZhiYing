from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from zhiying.knowledge.cloud_payload import (
    CloudPayloadError,
    build_cloud_payload,
    plan_payload_batches,
    validate_cloud_response,
)
from zhiying.knowledge.course_ir import build_course_ir
from zhiying.knowledge.organizer import organize_cloud
from zhiying.knowledge.schema import (
    ChapterPlan,
    LessonPlan,
    UnitPlan,
    VisualEvidence,
    VisualNeed,
)
from zhiying.knowledge.source_blocks import build_cloud_source_blocks


class CloudPayloadTests(unittest.TestCase):
    def _fixture(self):
        segments = []
        unit_plans = []
        for index in range(1, 7):
            segment_id = f"seg_{index:05d}"
            plan_id = f"plan_{index:03d}"
            segments.append({
                "segment_id": segment_id,
                "start_seconds": float(index * 50),
                "end_seconds": float(index * 50 + 35),
                "text": f"第{index}个概念说明判断依据。如果满足条件{index}，则执行步骤{index}。例如案例{index}用于核对边界。",
            })
            unit_plans.append(UnitPlan(
                plan_id=plan_id,
                title=f"概念与规则{index}",
                role="core",
                knowledge_types=["rule"],
                detail_level="standard",
                detail_reason=(
                    f"概念{index}是后续判断的前置条件，来源包含定义、判断条件、操作顺序、案例和边界；"
                    "旧版写作计划会把这些说明、课程级预算依据、错误风险、迁移价值和来源容量全部展开给 writer。"
                    "为了保证课程讲义完整，要求逐项说明前提、机制、条件、步骤、案例、例外和复习提示；"
                    "同时不得补充外部事实，必须保留数字、否定词、方向词和来源引用。"
                ),
                source_segment_ids=[segment_id],
                required_facets=["prerequisite", "mechanism", "condition", "procedure", "example", "exception", "pitfall"],
                visual_need=VisualNeed(
                    required=True,
                    question=f"哪张图能直接展示概念{index}、条件{index}与边界之间的可见关系？",
                    role="explain",
                    target_count=1,
                    max_count=1,
                    sequence_mode="single",
                    explanation_depth="teaching_note",
                    success_criteria=[
                        f"画面中可辨认概念{index}",
                        f"画面中可辨认条件{index}",
                        "画面中可辨认对象与边界的相对位置",
                        "图片证据能够支持课堂中的判断关系",
                    ],
                    reason="该知识点包含明确图表对象、判断关系和课堂指示语，需要像素证据而不是时间接近。",
                ),
            ))
        transcript = {"segments": segments}
        lesson_plan = LessonPlan(
            domain="测试领域",
            core_thread="六个概念的判断规则",
            terminology=["概念", "条件", "边界"],
            chapters=[ChapterPlan(chapter_id="chapter_001", title="规则", unit_plans=unit_plans)],
        )
        visual = VisualEvidence(
            evidence_id="ve_001",
            decision="select",
            matched_knowledge_point_id="plan_001",
            visual_role="explain",
            visible_evidence=["图中可见概念1与边界线"],
            visual_answer="概念1位于边界线左侧",
        )
        return lesson_plan, transcript, [visual]

    def test_payload_contains_only_compact_allowed_projection(self) -> None:
        plan, transcript, visuals = self._fixture()
        payload = build_cloud_payload(build_course_ir(plan, transcript, visuals))
        encoded = payload.json_text()
        for forbidden in (
            "image_path", "base64", "image_sha256", "perceptual_hash",
            "runtime_events", "no_match", "document_json",
        ):
            self.assertNotIn(forbidden, encoded)
        self.assertEqual(len(payload.visuals), 1)
        self.assertEqual(set(payload.visuals[0]), {"visual_id", "unit_id", "role", "facts", "answer"})

    def test_compact_payload_is_at_most_sixty_percent_of_legacy_dynamic_context(self) -> None:
        plan, transcript, visuals = self._fixture()
        payload = build_cloud_payload(build_course_ir(plan, transcript, visuals))
        source, _ = build_cloud_source_blocks(transcript)
        legacy_chars = len(source + json.dumps(
            {"plan": plan.to_dict(), "visuals": [item.to_dict() for item in visuals]},
            ensure_ascii=False,
        ))
        self.assertLessEqual(payload.char_count, int(legacy_chars * 0.60))

    def test_batches_are_planned_before_requests_and_preserve_valid_ids(self) -> None:
        plan, transcript, visuals = self._fixture()
        payload = build_cloud_payload(build_course_ir(plan, transcript, visuals))
        batches = plan_payload_batches(payload, max_input_chars=900, max_output_tokens=900)
        self.assertGreater(len(batches), 1)
        self.assertTrue(all(batch.char_count <= 900 for batch in batches))
        self.assertEqual(
            set().union(*(batch.allowed_ids.unit_ids for batch in batches)),
            payload.allowed_ids.unit_ids,
        )

    def test_batches_respect_configured_unit_count_limit(self) -> None:
        plan, transcript, visuals = self._fixture()
        payload = build_cloud_payload(build_course_ir(plan, transcript, visuals))

        batches = plan_payload_batches(
            payload,
            max_input_chars=60000,
            max_output_tokens=12000,
            max_units_per_batch=2,
        )

        self.assertEqual(len(batches), 3)
        self.assertTrue(all(len(batch.allowed_ids.unit_ids) <= 2 for batch in batches))

    def test_response_with_unknown_id_is_rejected(self) -> None:
        plan, transcript, visuals = self._fixture()
        payload = build_cloud_payload(build_course_ir(plan, transcript, visuals))
        with self.assertRaises(CloudPayloadError):
            validate_cloud_response({"plan_id": "plan_unknown"}, payload.allowed_ids)

    def test_local_path_in_visual_fact_is_rejected(self) -> None:
        plan, transcript, visuals = self._fixture()
        visuals[0].visual_answer = "D:/private/frame.jpg"
        with self.assertRaises(CloudPayloadError):
            build_cloud_payload(build_course_ir(plan, transcript, visuals))

    def test_over_budget_prompt_never_constructs_cloud_client(self) -> None:
        plan, transcript, visuals = self._fixture()
        class NoCloud:
            def request_json_with_info(self, *_args, **_kwargs):
                raise AssertionError("超预算时不得调用 CloudJsonPort")

        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(CloudPayloadError):
                organize_cloud(
                    plan,
                    transcript,
                    "推荐",
                    {
                        "budget": {"max_input_chars": 100, "max_output_tokens": 900},
                        "_cloud_payload_audit_path": str(Path(temp_dir) / "cloud-payload.json"),
                    },
                    visual_evidence=visuals,
                    cloud_port=NoCloud(),
                )


if __name__ == "__main__":
    unittest.main()
