"""CP61-1 责任测试：EditorialPolicy v1 与 LocalIntentCompiler。"""
from __future__ import annotations

import unittest

from zhiying.editorial.intent import compile_editorial_policy
from zhiying.editorial.policy import (
    EditorialConstraint,
    EditorialPolicy,
    constraint_code,
)
from zhiying.knowledge.editorial import brief_from_text


def _codes(policy: EditorialPolicy, kind: str) -> set[str]:
    return {constraint_code(row) for row in getattr(policy, kind)}


class EditorialPolicyTests(unittest.TestCase):
    def test_default_brief_yields_satisfied_policy_without_forbidden(self) -> None:
        policy = compile_editorial_policy(brief_from_text("希望形成适合系统学习和复习的正式课程资料"))
        self.assertEqual(policy.version, 1)
        self.assertEqual(policy.status, "satisfied")
        self.assertEqual(_codes(policy, "forbidden"), set())

    def test_forbid_legacy_sections(self) -> None:
        policy = compile_editorial_policy(brief_from_text(
            "不要内容导览、不要学习目标、不要课程复习，也不要固定章节编号",
        ))
        self.assertEqual(
            _codes(policy, "forbidden"),
            {"overview", "learning_objectives", "review", "fixed_numbering"},
        )

    def test_require_overview(self) -> None:
        policy = compile_editorial_policy(brief_from_text("需要内容导览"))
        self.assertIn("overview", _codes(policy, "required"))

    def test_structure_preferences(self) -> None:
        case = compile_editorial_policy(brief_from_text("例题加思路讲解"))
        self.assertEqual(case.structure_preference, "case_solution")
        concept = compile_editorial_policy(brief_from_text("概念定义和性质"))
        self.assertEqual(concept.structure_preference, "concept_properties")
        procedure = compile_editorial_policy(brief_from_text("按操作步骤讲解"))
        self.assertEqual(procedure.structure_preference, "procedure")
        comparison = compile_editorial_policy(brief_from_text("多讲对比和区别"))
        self.assertEqual(comparison.structure_preference, "comparison")

    def test_density_levels(self) -> None:
        self.assertEqual(compile_editorial_policy(brief_from_text("内容精简一些")).density, "concise")
        self.assertEqual(compile_editorial_policy(brief_from_text("内容推荐")).density, "recommended")
        self.assertEqual(compile_editorial_policy(brief_from_text("内容丰富详细一些")).density, "rich")

    def test_visual_preferences(self) -> None:
        less = compile_editorial_policy(brief_from_text("少放图片"))
        self.assertEqual(less.visual_preference, "image_less")
        more = compile_editorial_policy(brief_from_text("图片多一些"))
        self.assertEqual(more.visual_preference, "image_more")
        none = compile_editorial_policy(brief_from_text("不要图片"))
        self.assertEqual(none.visual_preference, "image_forbidden")

    def test_formula_and_table_and_source_preferences(self) -> None:
        self.assertEqual(compile_editorial_policy(brief_from_text("公式优先")).formula_preference, "formula_priority")
        self.assertEqual(compile_editorial_policy(brief_from_text("多用表格")).table_preference, "table_priority")
        self.assertEqual(compile_editorial_policy(brief_from_text("显示来源")).source_display, "show")

    def test_unsupported_complex_intent_is_partially_satisfied(self) -> None:
        policy = compile_editorial_policy(brief_from_text("讲义风格，双栏排版，加入名人名言"))
        self.assertEqual(policy.status, "partially_satisfied")
        self.assertTrue(policy.unmatched_hints)

    def test_constraint_carries_source_span_and_predicate(self) -> None:
        policy = compile_editorial_policy(brief_from_text("不要内容导览"))
        row = next(item for item in policy.forbidden if constraint_code(item) == "overview")
        self.assertIn("内容导览", row.source_span)
        self.assertTrue(row.predicate)

    def test_policy_roundtrip(self) -> None:
        policy = compile_editorial_policy(brief_from_text("不要课程复习，公式优先，例题加思路"))
        restored = EditorialPolicy.from_dict(policy.to_dict())
        self.assertEqual(restored.to_dict(), policy.to_dict())

    def test_forbidden_legacy_sections_detected_in_component_tree(self) -> None:
        policy = compile_editorial_policy(brief_from_text("不要内容导览、不要学习目标、不要课程复习"))
        # 模拟 V6.0 renderer 注入的旧栏目组件树
        injected = [
            {"type": "container", "semantic_role": "overview", "component_id": "overview_1"},
            {"type": "container", "semantic_role": "learning_objectives", "component_id": "lo_1"},
            {"type": "container", "semantic_role": "review", "component_id": "review_1"},
        ]
        hits = policy.forbidden_hits(injected)
        self.assertEqual(set(hits), {"overview", "learning_objectives", "review"})
        clean = [{"type": "container", "semantic_role": "chapter", "component_id": "ch_1"}]
        self.assertEqual(policy.forbidden_hits(clean), [])


if __name__ == "__main__":
    unittest.main()
