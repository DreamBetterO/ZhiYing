from __future__ import annotations

import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from zhiying.documents.v3 import (
    build_document_plan, compile_document_v3, compose_chapters,
    repair_chapters, v3_to_v2, validate_chapters, validate_document_v3,
)


class DocumentV3Tests(unittest.TestCase):
    def fixture(self):
        return {
            "schema_version": 2,
            "metadata": {
                "video_id": "lesson", "document_title": "课程", "title": "课程",
                "source_video": "lesson.mp4", "duration_label": "00:01:00",
            },
            "overview": "导览",
            "sections": [{"title": "第一章", "summary": "摘要", "knowledge_points": [{
                "statement": "定义", "explanation": "完整解释",
                "content_blocks": [{"block_id": "b1", "type": "equation", "text": "x^2"}],
                "steps": ["第一步"], "source_refs": {"start_seconds": 3},
            }]}],
        }

    def test_local_and_cloud_plan_share_schema_and_compile_to_v3(self) -> None:
        local = build_document_plan(self.fixture(), mode="local")
        cloud = build_document_plan(self.fixture(), mode="cloud")
        self.assertEqual(set(local), set(cloud))
        chapters = compose_chapters(local)
        repaired = repair_chapters(chapters, validate_chapters(chapters))
        document = compile_document_v3(local, repaired)
        validate_document_v3(document)
        self.assertEqual(document["schema_version"], 3)
        point = document["components"][0]["children"][-1]
        self.assertTrue(any(child["type"] == "equation" for child in point["children"]))
        self.assertEqual(v3_to_v2(document)["sections"][0]["knowledge_points"][0]["statement"], "定义")

    def test_unknown_component_is_rejected(self) -> None:
        plan = build_document_plan(self.fixture())
        document = compile_document_v3(plan, compose_chapters(plan))
        document["components"][0]["children"].append({"type": "arbitrary_xml"})
        with self.assertRaisesRegex(ValueError, "未知"):
            validate_document_v3(document)

    def test_document_adapter_renders_v3_through_temporary_v2_read_view(self) -> None:
        from zhiying.render import DocumentAdapter

        plan = build_document_plan(self.fixture())
        document = compile_document_v3(plan, compose_chapters(plan))
        document["transcript"] = [{
            "start_seconds": 0.0, "end_seconds": 1.0, "text": "不应进入精简文档",
        }]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "document-v3.json"
            source.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
            markdown, word, pdf = root / "lesson.md", root / "lesson.docx", root / "lesson.pdf"
            adapter = DocumentAdapter(root, include_transcript=False)
            adapter.render_markdown(document, markdown)

            def fake_docx(path, output, _root, **_kwargs):
                rendered = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(rendered["schema_version"], 2)
                self.assertFalse(rendered["render_options"]["include_full_transcript"])
                output.write_bytes(b"docx")

            def fake_pdf(_word, output, legacy, **_kwargs):
                self.assertEqual(legacy["schema_version"], 2)
                self.assertFalse(legacy["render_options"]["include_full_transcript"])
                output.write_bytes(b"pdf")
                return "built_in"

            with patch("zhiying.render.render_docx", side_effect=fake_docx), patch(
                "zhiying.render.convert_docx_to_pdf", side_effect=fake_pdf,
            ):
                adapter.render_word(source, word, cancel_check=lambda: False)
                mode = adapter.render_pdf(document, word, pdf, cancel_check=lambda: False)
            self.assertIn("课程", markdown.read_text(encoding="utf-8"))
            self.assertEqual(mode, "built_in")
            self.assertFalse(word.with_suffix(".render-v2.json").exists())

    def test_document_adapter_creates_word_staging_parent_before_v3_projection(self) -> None:
        """Each graph render node owns a fresh staging tree with no pre-created subfolders."""
        from zhiying.render import DocumentAdapter

        plan = build_document_plan(self.fixture())
        document = compile_document_v3(plan, compose_chapters(plan))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "document-v3.json"
            source.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
            word = root / "render.word" / "state" / "render" / "lesson.docx"

            def fake_docx(path, output, _root, **_kwargs):
                self.assertTrue(path.is_file())
                output.write_bytes(b"docx")

            with patch("zhiying.render.render_docx", side_effect=fake_docx):
                DocumentAdapter(root).render_word(source, word, cancel_check=lambda: False)

            self.assertEqual(word.read_bytes(), b"docx")
            self.assertFalse(word.with_suffix(".render-v2.json").exists())


if __name__ == "__main__":
    unittest.main()
