from __future__ import annotations

import unittest

from zhiying.execution.decision_policy import (
    AdvisorAdmission, LocalDecisionPolicy, VisualNeedLevel,
)
from zhiying.knowledge.schema import UnitPlan, VisualNeed, VisualQuestion


class V6DecisionPolicyTests(unittest.TestCase):
    def test_visual_need_has_none_supportive_and_required_levels(self) -> None:
        policy = LocalDecisionPolicy()
        self.assertEqual(policy.visual_need(UnitPlan(plan_id="none")), VisualNeedLevel.NONE)
        supportive = UnitPlan(
            plan_id="supportive",
            visual_questions=[VisualQuestion(question_id="q", unit_id="supportive", question="图中是什么")],
        )
        self.assertEqual(policy.visual_need(supportive), VisualNeedLevel.SUPPORTIVE)
        required = UnitPlan(plan_id="required", visual_need=VisualNeed(required=True, question="定位公式"))
        self.assertEqual(policy.visual_need(required), VisualNeedLevel.REQUIRED)

    def test_model_advisor_admission_is_task_whitelisted_and_never_decides_permissions(self) -> None:
        admission = AdvisorAdmission({"qwen3-vl-2b": {"visual_need_advice", "candidate_ranking"}})
        self.assertTrue(admission.allowed("qwen3-vl-2b", "candidate_ranking"))
        self.assertFalse(admission.allowed("qwen3-vl-2b", "permission_decision"))
        self.assertFalse(admission.allowed("unknown", "candidate_ranking"))

    def test_uncertain_asr_spans_are_bounded_and_evidence_driven(self) -> None:
        segments = [
            {"id": "seg_1", "text": "普通说明", "confidence": 0.95},
            {"id": "seg_2", "text": "这里是 x 等于", "confidence": 0.42},
            {"id": "seg_3", "text": "公式分母可能听错", "confidence": 0.8},
        ]
        spans = LocalDecisionPolicy().uncertain_asr_spans(segments, max_spans=2)
        self.assertEqual([row["id"] for row in spans], ["seg_2", "seg_3"])


if __name__ == "__main__":
    unittest.main()
