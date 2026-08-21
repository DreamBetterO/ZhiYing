"""CP61-1 责任测试：DocumentBlueprint v2 Schema/validator。"""
from __future__ import annotations

import unittest

from video_study.editorial.blueprint import (
    BlueprintChapter,
    DocumentBlueprint,
    validate_blueprint,
)


def _sample_blueprint(**overrides) -> DocumentBlueprint:
    chapters = [
        BlueprintChapter(
            chapter_id="chapter_001",
            title="原函数的定义",
            mode="concept",
            unit_refs=["plan_001"],
            component_intents=["definition", "properties", "boundary"],
            layout_hint="full_width",
            depth="standard",
            target_chars=360,
        ),
    ]
    return DocumentBlueprint(
        blueprint_id="bp_001",
        policy_version=1,
        evidence_version=1,
        capability_version="renderer-capability-v1",
        document_type="course_notes",
        audience="学习者",
        purpose="系统学习与复习",
        density="recommended",
        chapters=chapters,
        source_refs=["seg_00001", "seg_00002"],
        layout_hints=["full_width"],
        constraint_mapping={"overview": "forbidden", "review": "forbidden"},
        **overrides,
    )


class BlueprintTests(unittest.TestCase):
    def test_valid_blueprint_passes_validation(self) -> None:
        blueprint = _sample_blueprint()
        validate_blueprint(blueprint, known_unit_ids={"plan_001"})

    def test_legacy_source_section_is_forbidden(self) -> None:
        blueprint = _sample_blueprint()
        blueprint.chapters[0].component_intents = ["source_section"]  # type: ignore[attr-defined]
        with self.assertRaisesRegex(ValueError, "source_section"):
            validate_blueprint(blueprint, known_unit_ids={"plan_001"})

    def test_unknown_unit_ref_is_rejected(self) -> None:
        blueprint = _sample_blueprint()
        blueprint.chapters[0].unit_refs = ["plan_999"]  # type: ignore[attr-defined]
        with self.assertRaisesRegex(ValueError, "引用不存在的 unit"):
            validate_blueprint(blueprint, known_unit_ids={"plan_001"})

    def test_unknown_layout_hint_is_rejected(self) -> None:
        blueprint = _sample_blueprint()
        blueprint.layout_hints = ["arbitrary_coordinates"]  # type: ignore[attr-defined]
        with self.assertRaisesRegex(ValueError, "布局"):
            validate_blueprint(blueprint, known_unit_ids={"plan_001"})

    def test_forbidden_fields_are_rejected(self) -> None:
        blueprint = _sample_blueprint()
        blueprint.design_tokens = ["docx_xml:<w:p>"]  # type: ignore[attr-defined]
        with self.assertRaisesRegex(ValueError, "docx_xml"):
            validate_blueprint(blueprint, known_unit_ids={"plan_001"})

    def test_absolute_local_path_is_rejected(self) -> None:
        blueprint = _sample_blueprint()
        blueprint.source_refs = ["D:/Study/实习/视频归纳/workspace/x"]  # type: ignore[attr-defined]
        with self.assertRaisesRegex(ValueError, "本地绝对路径"):
            validate_blueprint(blueprint, known_unit_ids={"plan_001"})

    def test_stable_component_ids_required(self) -> None:
        blueprint = _sample_blueprint()
        blueprint.chapters[0].chapter_id = ""  # type: ignore[attr-defined]
        with self.assertRaisesRegex(ValueError, "chapter_id"):
            validate_blueprint(blueprint, known_unit_ids={"plan_001"})

    def test_schema_version_is_two(self) -> None:
        blueprint = _sample_blueprint()
        blueprint.schema_version = 1  # type: ignore[attr-defined]
        with self.assertRaisesRegex(ValueError, "schema_version"):
            validate_blueprint(blueprint, known_unit_ids={"plan_001"})

    def test_roundtrip_serialization(self) -> None:
        blueprint = _sample_blueprint()
        restored = DocumentBlueprint.from_dict(blueprint.to_dict())
        self.assertEqual(restored.to_dict(), blueprint.to_dict())
        validate_blueprint(restored, known_unit_ids={"plan_001"})

    def test_blueprint_contains_no_markdown_or_xml_body(self) -> None:
        blueprint = _sample_blueprint()
        text = str(blueprint.to_dict())
        self.assertNotIn("## ", text)
        self.assertNotIn("<w:", text)


if __name__ == "__main__":
    unittest.main()
