"""CP61-1 责任测试：LocalBlueprintPolicy + LocalDocumentComposer + 最小 repair（本地编辑链）。"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from zhiying.editorial.blueprint import DocumentBlueprint, validate_blueprint
from zhiying.editorial.document import validate_document_v31
from zhiying.editorial.evidence import EvidenceCorrectionOverlay, detect_local_corrections, transcript_digest
from zhiying.editorial.intent import compile_editorial_policy
from zhiying.editorial.local import (
    build_local_blueprint,
    compose_local_document,
    local_deterministic_repair,
)
from zhiying.knowledge.editorial import brief_from_text
from zhiying.knowledge.schema import LessonPlan, UnitPlan, ChapterPlan

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "v61"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _plan_from_fixture(fixture: dict) -> LessonPlan:
    chapters = []
    for index, unit_data in enumerate(fixture["plan"]["units"], start=1):
        chapter = ChapterPlan(
            chapter_id=f"chapter_{index:03d}",
            title=unit_data["title"],
            source_segment_ids=unit_data.get("source_segment_ids", []),
            unit_plans=[UnitPlan(
                plan_id=unit_data["plan_id"],
                title=unit_data["title"],
                role=unit_data.get("role", "core"),
                knowledge_types=[unit_data.get("type", "concept")],
                source_segment_ids=unit_data.get("source_segment_ids", []),
            )],
        )
        chapters.append(chapter)
    return LessonPlan(chapters=chapters, course_form="concept_lecture")


class LocalEditorTests(unittest.TestCase):
    def test_local_blueprint_respects_forbidden_legacy_sections(self) -> None:
        fixture = _load_fixture("math_concept.json")
        policy = compile_editorial_policy(brief_from_text(
            "不要内容导览、不要学习目标、不要课程复习、不要固定章节编号",
        ))
        plan = _plan_from_fixture(fixture)
        blueprint = build_local_blueprint(plan, policy)
        validate_blueprint(blueprint, known_unit_ids={"plan_001", "plan_002"})
        self.assertNotIn("overview", blueprint.front_matter_policy.get("sections", []))
        self.assertNotIn("learning_objectives", blueprint.front_matter_policy.get("sections", []))
        self.assertFalse(blueprint.navigation_policy.get("fixed_numbering", False))
        self.assertEqual(blueprint.constraint_mapping.get("overview"), "forbidden")

    def test_local_blueprint_mode_detection(self) -> None:
        case_fixture = _load_fixture("math_example.json")
        concept_fixture = _load_fixture("math_concept.json")
        case_bp = build_local_blueprint(_plan_from_fixture(case_fixture), compile_editorial_policy(brief_from_text("例题加思路")))
        concept_bp = build_local_blueprint(_plan_from_fixture(concept_fixture), compile_editorial_policy(brief_from_text("概念定义和性质")))
        self.assertEqual(case_bp.chapters[0].mode, "case")
        self.assertEqual(concept_bp.chapters[0].mode, "concept")

    def test_local_blueprint_references_only_existing_units(self) -> None:
        fixture = _load_fixture("math_concept.json")
        blueprint = build_local_blueprint(_plan_from_fixture(fixture), compile_editorial_policy(brief_from_text("形成适合系统学习和复习的正式课程资料")))
        validate_blueprint(blueprint, known_unit_ids={"plan_001", "plan_002"})

    def test_composed_document_is_valid_v31_without_cloud(self) -> None:
        fixture = _load_fixture("math_concept.json")
        policy = compile_editorial_policy(brief_from_text("不要内容导览、不要学习目标、不要课程复习"))
        plan = _plan_from_fixture(fixture)
        overlay = EvidenceCorrectionOverlay(
            version=1, transcript_digest=transcript_digest(fixture["transcript"]),
            corrections=detect_local_corrections(fixture["transcript"]),
        )
        units = [{
            "unit_id": "unit_0001", "type": "concept", "title": "原函数的定义",
            "definition_or_conclusion": "如果有一个大F，它的导数等于小F，那这个大F就叫做小F的一个圆寒数，小F加上任意长数也还是圆寒数",
            "rules": [], "procedure": [], "pitfalls": [], "unresolved": [],
            "plan_id": "plan_001", "source_refs": {"segment_ids": ["seg_00002", "seg_00003"]},
        }, {
            "unit_id": "unit_0002", "type": "concept", "title": "原函数的存在性",
            "definition_or_conclusion": "如果小F在区间上连续，那么它一定有圆寒数；小F的便上线级分就是一个可导寒数",
            "rules": [], "procedure": [], "pitfalls": [], "unresolved": [],
            "plan_id": "plan_002", "source_refs": {"segment_ids": ["seg_00004", "seg_00005"]},
        }]
        blueprint = build_local_blueprint(plan, policy)
        document = compose_local_document(
            blueprint=blueprint, units=units, overlay=overlay, plan=plan,
            visual_evidence=[], metadata={"video_id": "math_concept"},
        )
        validate_document_v31(document)
        self.assertEqual(document["contract_version"], "document-v3.1")

        body_text = json.dumps(document["components"], ensure_ascii=False)
        for error in ("圆寒数", "长数", "级分", "便上线级分", "寒数"):
            self.assertNotIn(error, body_text, f"正文仍含 ASR 错词：{error}")
        self.assertEqual(document["provenance"]["blueprint"], "local_deterministic")
        self.assertEqual(document["provenance"]["chapter_writing"]["chapter_001"], "local_deterministic")

    def test_composed_document_contains_equation_component(self) -> None:
        fixture = _load_fixture("math_example.json")
        plan = _plan_from_fixture(fixture)
        overlay = EvidenceCorrectionOverlay(
            version=1, transcript_digest=transcript_digest(fixture["transcript"]),
            corrections=detect_local_corrections(fixture["transcript"]),
        )
        units = [{
            "unit_id": "unit_0001", "type": "case", "title": "例题：求不定积分 x^2 dx",
            "definition_or_conclusion": "x 的 n 次方的积分等于 x 的 n 加一次方除以 n 加一，再加长数 C；所以 ∫x^2 dx = x^3/3 + C",
            "rules": [], "procedure": [], "pitfalls": [], "unresolved": [],
            "plan_id": "plan_001", "source_refs": {"segment_ids": ["seg_00001"]},
        }]
        blueprint = build_local_blueprint(plan, compile_editorial_policy(brief_from_text("例题加思路")))
        document = compose_local_document(
            blueprint=blueprint, units=units, overlay=overlay, plan=plan,
            visual_evidence=[], metadata={"video_id": "math_example"},
        )
        equations = _collect(document["components"], "equation")
        self.assertGreaterEqual(len(equations), 1)
        self.assertIn("∫", equations[0].get("latex", ""))
        self.assertNotIn("长数", json.dumps(document["components"], ensure_ascii=False))

    def test_local_fallback_renders_rich_content_blocks_instead_of_dropping_them(self) -> None:
        fixture = _load_fixture("math_example.json")
        plan = _plan_from_fixture(fixture)
        overlay = EvidenceCorrectionOverlay(
            version=1, transcript_digest=transcript_digest(fixture["transcript"]), corrections=[],
        )
        units = [{
            "unit_id": "unit_0001", "plan_id": "plan_001", "title": "积分例题",
            "definition_or_conclusion": "简短摘要", "source_refs": {"segment_ids": ["seg_00001"]},
            "content_blocks": [
                {"block_id": "b1", "type": "paragraph", "text": "这是必须保留的完整概念讲解。", "items": []},
                {"block_id": "b2", "type": "example", "text": "例题完整解法：先换元，再分部积分。", "items": []},
                {"block_id": "b3", "type": "pitfall", "text": "易错点：定积分换元后必须同步换限。", "items": []},
                {"block_id": "b4", "type": "steps", "text": "", "items": ["第一步整理被积式", "第二步完成换元"]},
            ],
        }]
        blueprint = build_local_blueprint(plan, compile_editorial_policy(brief_from_text("例题加思路")))
        document = compose_local_document(
            blueprint=blueprint, units=units, overlay=overlay, plan=plan,
            visual_evidence=[], metadata={"video_id": "rich-fallback"},
        )
        text = json.dumps(document["components"], ensure_ascii=False)
        self.assertIn("完整概念讲解", text)
        self.assertIn("例题完整解法", text)
        self.assertIn("定积分换元后必须同步换限", text)
        self.assertIn("第二步完成换元", text)
        self.assertGreaterEqual(len(_collect(document["components"], "callout")), 2)
        self.assertGreaterEqual(len(_collect(document["components"], "list")), 1)

    def test_image_component_from_selected_visual_evidence(self) -> None:
        fixture = _load_fixture("strong_visual.json")
        plan = _plan_from_fixture(fixture)
        overlay = EvidenceCorrectionOverlay(version=1, transcript_digest=transcript_digest(fixture["transcript"]), corrections=[])
        units = [{
            "unit_id": "unit_0001", "type": "concept", "title": "中枢的边界与重叠区间",
            "definition_or_conclusion": "图上标出的这个区间就是重叠区间",
            "rules": [], "procedure": [], "pitfalls": [], "unresolved": [],
            "plan_id": "plan_001", "source_refs": {"segment_ids": ["seg_00001"]},
        }]
        blueprint = build_local_blueprint(plan, compile_editorial_policy(brief_from_text("形成适合系统学习和复习的正式课程资料")))
        document = compose_local_document(
            blueprint=blueprint, units=units, overlay=overlay, plan=plan,
            visual_evidence=fixture["visual_evidence"], metadata={"video_id": "strong_visual"},
        )
        images = _collect(document["components"], "image")
        self.assertGreaterEqual(len(images), 1)
        image = images[0]
        for field in ("visual_id", "role", "caption", "alt_text", "source_timestamp"):
            self.assertIn(field, image)
        self.assertTrue(image["source_refs"])

    def test_weak_visual_produces_no_image_components(self) -> None:
        fixture = _load_fixture("weak_visual.json")
        plan = _plan_from_fixture(fixture)
        overlay = EvidenceCorrectionOverlay(version=1, transcript_digest=transcript_digest(fixture["transcript"]), corrections=[])
        units = [{
            "unit_id": "unit_0001", "type": "concept", "title": "线性回归的参数估计",
            "definition_or_conclusion": "我们最小化残差平方和来估计参数",
            "rules": [], "procedure": [], "pitfalls": [], "unresolved": [],
            "plan_id": "plan_001", "source_refs": {"segment_ids": ["seg_00001"]},
        }]
        blueprint = build_local_blueprint(plan, compile_editorial_policy(brief_from_text("形成适合系统学习和复习的正式课程资料")))
        document = compose_local_document(
            blueprint=blueprint, units=units, overlay=overlay, plan=plan,
            visual_evidence=fixture["visual_evidence"], metadata={"video_id": "weak_visual"},
        )
        self.assertEqual(_collect(document["components"], "image"), [])

    def test_unscoped_selected_visual_is_not_duplicated_across_units(self) -> None:
        fixture = _load_fixture("strong_visual.json")
        plan = LessonPlan(chapters=[ChapterPlan(
            chapter_id="chapter_001", title="图示讲解",
            unit_plans=[
                UnitPlan(plan_id="plan_001", title="边界", knowledge_types=["concept"]),
                UnitPlan(plan_id="plan_002", title="区间", knowledge_types=["concept"]),
            ],
        )])
        units = [
            {"unit_id": f"unit_{index:04d}", "plan_id": f"plan_{index:03d}", "title": title,
             "definition_or_conclusion": title, "source_refs": {"segment_ids": ["seg_00001"]}}
            for index, title in ((1, "边界"), (2, "区间"))
        ]
        overlay = EvidenceCorrectionOverlay(
            version=1, transcript_digest=transcript_digest(fixture["transcript"]), corrections=[],
        )
        blueprint = build_local_blueprint(plan, compile_editorial_policy(brief_from_text("图片帮助理解")))
        document = compose_local_document(
            blueprint=blueprint, units=units, overlay=overlay, plan=plan,
            visual_evidence=fixture["visual_evidence"], metadata={"video_id": "strong_visual"},
        )
        self.assertLessEqual(len(_collect(document["components"], "image")), 1)

    def test_unresolved_asr_is_not_polished_into_rules(self) -> None:
        fixture = _load_fixture("math_concept.json")
        plan = _plan_from_fixture(fixture)
        overlay = EvidenceCorrectionOverlay(version=1, transcript_digest=transcript_digest(fixture["transcript"]), corrections=[])
        units = [{
            "unit_id": "unit_0001", "type": "concept", "title": "原函数的定义",
            "definition_or_conclusion": "那好了 就是说如果有一个大F 它的导数等于小F 那这个大F就叫做小F的一个圆寒数 就是说 好吧",
            "rules": ["那好了 圆寒数加长数都是圆寒数 就是说好吧"], "procedure": [], "pitfalls": [], "unresolved": [],
            "plan_id": "plan_001", "source_refs": {"segment_ids": ["seg_00002"]},
        }]
        blueprint = build_local_blueprint(plan, compile_editorial_policy(brief_from_text("形成适合系统学习和复习的正式课程资料")))
        document = compose_local_document(
            blueprint=blueprint, units=units, overlay=overlay, plan=plan,
            visual_evidence=[], metadata={"video_id": "degraded"},
        )
        text = json.dumps(document["components"], ensure_ascii=False)
        self.assertNotIn("圆寒数", text)
        self.assertNotIn("那好了", text)
        # 降级：unresolved 被保留而非伪装为规则/步骤
        unresolved = _collect(document["components"], "callout")
        self.assertTrue(any("待核对" in str(row.get("text", "")) for row in unresolved))

    def test_transcript_digest_unchanged_after_compose(self) -> None:
        fixture = _load_fixture("math_concept.json")
        before = transcript_digest(fixture["transcript"])
        plan = _plan_from_fixture(fixture)
        overlay = EvidenceCorrectionOverlay(
            version=1, transcript_digest=transcript_digest(fixture["transcript"]),
            corrections=detect_local_corrections(fixture["transcript"]),
        )
        units = [{
            "unit_id": "unit_0001", "type": "concept", "title": "原函数的定义",
            "definition_or_conclusion": "圆寒数", "rules": [], "procedure": [], "pitfalls": [],
            "unresolved": [], "plan_id": "plan_001", "source_refs": {"segment_ids": ["seg_00002"]},
        }]
        blueprint = build_local_blueprint(plan, compile_editorial_policy(brief_from_text("形成适合系统学习和复习的正式课程资料")))
        compose_local_document(
            blueprint=blueprint, units=units, overlay=overlay, plan=plan,
            visual_evidence=[], metadata={"video_id": "x"},
        )
        self.assertEqual(transcript_digest(fixture["transcript"]), before)

    def test_deterministic_repair_fixes_empty_heading(self) -> None:
        components = [{
            "type": "container", "component_id": "chapter_001", "semantic_role": "chapter",
            "title": "", "children": [
                {"type": "heading", "component_id": "h1", "semantic_role": "heading", "text": ""},
            ],
        }]
        repaired = local_deterministic_repair(components)
        self.assertEqual(repaired[0]["title"], "章节 1")
        self.assertEqual(repaired[0]["children"][0]["text"], "章节 1")


def _collect(components: list[dict], component_type: str) -> list[dict]:
    result = []
    for component in components:
        if component.get("type") == component_type:
            result.append(component)
        result.extend(_collect(component.get("children", []), component_type))
    return result


if __name__ == "__main__":
    unittest.main()
