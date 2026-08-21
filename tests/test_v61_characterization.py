"""CP61-0 失败特征测试（characterization）。

两部分的用途与生命周期：
- A 部分（test_v61_*，@unittest.expectedFailure）：断言 V6.1 目标合同行为，在 V6.0 上
  “按预期失败”，是后续 CP61-1/2/5 必须翻绿的回归测试（翻绿时移除 expectedFailure 装饰器）。
- B 部分（test_v610_*）：钉住 V6.0 当前失败事实（“明确证明旧行为”），随生产行为变更而更新/删除。

本文件只测试代码级旧行为；历史 Workspace/Output 会被用户后续运行合法更新，
不得作为可变测试夹具，也不得触发 ASR/VLM/云端请求。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import yaml

from video_study.document_v3 import build_document_plan, compile_document_v3, compose_chapters
from video_study.editorial.document import build_v31_document, make_component
from video_study.execution.artifacts import ArtifactId, ArtifactRef
from video_study.execution.contracts import StepOutcome, StepStatus
from video_study.execution.steps.coarse import RenderVerifyStep
from video_study.render_v31 import count_word_omml, render_docx_v31

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "docs" / "迭代升级" / "CP61-0黄金清单.yaml"


def _baseline() -> dict:
    return yaml.safe_load(BASELINE.read_text(encoding="utf-8"))

def _v2_document() -> dict:
    return {
        "schema_version": 2,
        "metadata": {
            "video_id": "lesson", "document_title": "课程", "title": "课程",
            "source_video": "lesson.mp4", "duration_label": "00:01:00",
        },
        "overview": "导览",
        "learning_objectives": ["掌握定义"],
        "review": {
            "knowledge_thread": "主线", "checklist": ["规则"],
            "open_questions": ["问题"],
        },
        "sections": [{"title": "第一章", "summary": "摘要", "knowledge_points": [{
            "statement": "定义", "explanation": "完整解释",
            "content_blocks": [{"block_id": "b1", "type": "equation", "text": "x^2"}],
            "steps": ["第一步"], "source_refs": {"start_seconds": 3},
        }]}],
    }


class V61CharacterizationContractTests(unittest.TestCase):
    """A 部分：V6.1 契约断言，在 V6.0 上按预期失败。"""

    @unittest.expectedFailure
    def test_v61_plan_is_not_legacy_wrapper(self) -> None:
        """document.plan 必须不是旧 section 的机械包装（无 source_section）。"""
        plan = build_document_plan(_v2_document())
        self.assertNotIn("source_section", plan["chapters"][0])

    @unittest.expectedFailure
    def test_v61_plan_layout_hints_are_not_all_full_width(self) -> None:
        """规划必须产生真实布局决策，而不是全部 full_width。"""
        plan = build_document_plan(_v2_document())
        self.assertFalse(all(row["layout_hint"] == "full_width" for row in plan["chapters"]))

    @unittest.expectedFailure
    def test_v61_renderer_does_not_inject_forbidden_legacy_sections(self) -> None:
        """用户 forbidden 内容导览/学习目标/课程复习后，renderer 不得自动注入。"""
        from video_study.render import DocumentAdapter

        plan = build_document_plan(_v2_document())
        document = compile_document_v3(plan, compose_chapters(plan))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "lesson.md"
            DocumentAdapter(Path(directory), include_transcript=False).render_markdown(document, output)
            markdown = output.read_text(encoding="utf-8")
        for forbidden in ("内容导览", "学习目标", "课程复习"):
            self.assertNotIn(forbidden, markdown)

    @unittest.expectedFailure
    def test_v61_render_does_not_downgrade_v3_to_v2(self) -> None:
        """生产渲染必须原生消费 v3，不得调用 v3_to_v2。"""
        from video_study.render import DocumentAdapter

        def _boom(document):
            raise AssertionError("v3_to_v2 在生产渲染中被调用")

        plan = build_document_plan(_v2_document())
        document = compile_document_v3(plan, compose_chapters(plan))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "lesson.md"
            with patch("video_study.document_v3.v3_to_v2", side_effect=_boom):
                DocumentAdapter(Path(directory), include_transcript=False).render_markdown(document, output)
            self.assertTrue(output.is_file())

    def test_v61_word_omml_matches_confirmed_equations(self) -> None:
        document = build_v31_document(
            metadata={"video_id": "math", "document_title": "公式"},
            components=[make_component(
                "equation", component_id="eq.1", semantic_role="equation", latex="x^2",
            )],
            provenance={"blueprint": "local_deterministic"},
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "document-v3.json"
            source.write_text(__import__("json").dumps(document), encoding="utf-8")
            docx = render_docx_v31(source, root / "math.docx", project_root=root)
            self.assertEqual(count_word_omml(docx), 1)

    def test_v61_degraded_units_not_labeled_cloud_summary(self) -> None:
        document = build_v31_document(
            metadata={"video_id": "local", "document_title": "本地结果"},
            components=[], provenance={"blueprint": "local_deterministic"},
        )
        self.assertNotEqual(document.get("mode"), "cloud_summary")

    def test_v61_render_verify_checks_content_and_parity(self) -> None:
        """render.verify 必须检查内容与跨格式语义，而不是只检查文件非空。"""
        output_artifact = ArtifactId("render.verify.output", ("state/render/lesson.md", "state/render/lesson.docx", "state/render/lesson.pdf"))
        step = RenderVerifyStep(output_artifact)
        with tempfile.TemporaryDirectory() as directory:
            staging = Path(directory) / "staging"
            staging.mkdir()
            (staging / "lesson.md").write_text("## 内容导览\n违禁栏目", encoding="utf-8")
            (staging / "lesson.docx").write_bytes(b"docx")
            (staging / "lesson.pdf").write_bytes(b"pdf")
            outcome = StepOutcome("render.verify", "run-1", StepStatus.SUCCEEDED, artifacts=(
                ArtifactRef(output_artifact, staging / "lesson.md"),
            ))
            with self.assertRaises(ValueError):
                step.validate(None, outcome)


class V610CharacterizationBehaviorPins(unittest.TestCase):
    """B 部分：钉住 V6.0 失败事实（当前通过，行为变更后更新）。"""

    def test_v610_plan_contains_legacy_source_section(self) -> None:
        plan = build_document_plan(_v2_document())
        section = plan["chapters"][0]["source_section"]
        self.assertEqual(section["title"], "第一章")

    def test_v610_plan_layout_all_full_width(self) -> None:
        plan = build_document_plan(_v2_document())
        self.assertTrue(all(row["layout_hint"] == "full_width" for row in plan["chapters"]))

    def test_v610_renderer_downgrades_v3_to_v2(self) -> None:
        from video_study import document_v3 as _document_v3
        from video_study.render import DocumentAdapter

        plan = build_document_plan(_v2_document())
        document = compile_document_v3(plan, compose_chapters(plan))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "lesson.md"
            with patch("video_study.document_v3.v3_to_v2", wraps=_document_v3.v3_to_v2) as adapter:
                DocumentAdapter(Path(directory), include_transcript=False).render_markdown(document, output)
                adapter.assert_called_once()
            markdown = output.read_text(encoding="utf-8")
        for injected in ("内容导览", "学习目标", "课程复习"):
            self.assertIn(injected, markdown)

    def test_v610_frozen_docx_omml_is_zero(self) -> None:
        outputs = _baseline()["frozen_outputs"]
        self.assertEqual(outputs["高数-定积分定义-be06877a42cf"]["docx"]["word_omml"], 0)
        self.assertEqual(outputs["高数-例题讲解-a5c1f2d59bf3"]["docx"]["word_omml"], 0)

    def test_v610_frozen_degraded_sample_document_mode_is_cloud_summary(self) -> None:
        baseline = _baseline()
        notes = baseline["frozen_workspaces"]["高数-定积分定义-be06877a42cf"]["pipeline_state_notes"]
        evidence = baseline["known_problems"]["degraded_units_masked_as_cloud_summary"]["evidence"]
        self.assertTrue(any("status=degraded" in note for note in notes))
        self.assertIn("mode=cloud_summary", evidence)

    @unittest.expectedFailure
    def test_v610_render_verify_accepts_nonempty_files_with_forbidden_content(self) -> None:
        output_artifact = ArtifactId("render.verify.output", ("state/render/lesson.md", "state/render/lesson.docx", "state/render/lesson.pdf"))
        step = RenderVerifyStep(output_artifact)
        with tempfile.TemporaryDirectory() as directory:
            staging = Path(directory) / "staging"
            staging.mkdir()
            (staging / "lesson.md").write_text("## 内容导览\n违禁栏目", encoding="utf-8")
            (staging / "lesson.docx").write_bytes(b"docx")
            (staging / "lesson.pdf").write_bytes(b"pdf")
            outcome = StepOutcome("render.verify", "run-1", StepStatus.SUCCEEDED, artifacts=(
                ArtifactRef(output_artifact, staging / "lesson.md"),
            ))
            step.validate(None, outcome)  # 当前只校验非空，不抛异常


if __name__ == "__main__":
    unittest.main()
