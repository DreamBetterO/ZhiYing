"""CP61-2 责任测试：Document v3.1 原生三端渲染（Markdown/Word/PDF）。"""
from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from PIL import Image

from video_study.editorial.document import build_v31_document, make_component
from video_study.render import DocumentAdapterV31
from video_study.render_v31 import (
    component_statistics,
    render_docx_v31,
    render_markdown_v31,
    render_pdf_fallback_v31,
)

LEGACY_LABELS = ("内容导览", "学习目标", "课程复习", "看图重点", "这张图帮助理解", "固定章节编号")


def _v31_document(**overrides) -> dict:
    components = [
        make_component("heading", component_id="ch1.h", semantic_role="heading", text="第一章 原函数", level=2),
        make_component("paragraph", component_id="ch1.p1", semantic_role="paragraph", text="定义文本", source_refs={"segment_ids": ["seg_00001"]}),
        make_component("equation", component_id="ch1.eq1", semantic_role="equation", latex="\\int x^2 \\, dx = \\frac{x^3}{3} + C", source_refs={"segment_ids": ["seg_00002"]}),
        make_component("list", component_id="ch1.l1", semantic_role="key_points", items=["性质一", "性质二"], source_refs={"segment_ids": ["seg_00003"]}),
        make_component("callout", component_id="ch1.c1", semantic_role="unresolved", text="待核对来源", source_refs={"segment_ids": ["seg_00004"]}),
        make_component("page_break", component_id="ch1.pb1", semantic_role="page_break"),
        make_component("source_reference", component_id="ch1.src", semantic_role="source_reference",
                       source_refs={"segment_ids": ["seg_00001"]}, links=[{"label": "00:00:05", "url": "video-study://play/lesson?t=5"}]),
    ]
    return build_v31_document(
        metadata={"video_id": "lesson", "document_title": "课程", "duration_label": "00:01:00"},
        components=components,
        provenance={"blueprint": "local_deterministic"},
        **overrides,
    )


class NativeMarkdownTests(unittest.TestCase):
    def test_component_mapping_parameterized(self) -> None:
        document = _v31_document()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "lesson.md"
            render_markdown_v31(document, output, project_root=Path(directory))
            markdown = output.read_text(encoding="utf-8")
        self.assertIn("## 第一章 原函数", markdown)
        self.assertIn("定义文本", markdown)
        self.assertIn("$$\n\\int x^2 \\, dx = \\frac{x^3}{3} + C\n$$", markdown)
        self.assertIn("- 性质一", markdown)
        self.assertIn("> 待核对来源", markdown)
        self.assertIn("[▶ 回看来源 · 00:00:05](video-study://play/lesson?t=5)", markdown)

    def test_markdown_does_not_inject_legacy_labels(self) -> None:
        document = _v31_document()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "lesson.md"
            render_markdown_v31(document, output, project_root=Path(directory))
            markdown = output.read_text(encoding="utf-8")
        for label in LEGACY_LABELS:
            self.assertNotIn(label, markdown)

    def test_markdown_image_uses_relative_path_not_absolute(self) -> None:
        document = _v31_document()
        document["components"].append(make_component(
            "image", component_id="ch1.img", semantic_role="image",
            visual_id="ve_001", role="explain", caption="图注", alt_text="alt",
            source_timestamp=10.0, source_refs={"segment_ids": ["seg_00001"]},
        ))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "images").mkdir()
            (root / "images" / "candidate_00001.jpg").write_bytes(b"jpg")
            output = root / "lesson.md"
            render_markdown_v31(
                document, output, project_root=root,
                figure_map={"ve_001": {"path": str(root / "images" / "candidate_00001.jpg"), "width": 1280, "height": 720}},
            )
            markdown = output.read_text(encoding="utf-8")
        self.assertIn("![图注](images/candidate_00001.jpg)", markdown)
        self.assertNotIn("D:", markdown)


class NativeWordTests(unittest.TestCase):
    def test_word_renders_omml_and_no_legacy_labels(self) -> None:
        document = _v31_document()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "document-v3.json"
            source.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
            word = root / "lesson.docx"
            render_docx_v31(source, word, project_root=root, cancel_check=lambda: False)
            self.assertGreater(word.stat().st_size, 0)
            with zipfile.ZipFile(word) as archive:
                document_xml = archive.read("word/document.xml").decode("utf-8")
        self.assertGreaterEqual(len(__import__("re").findall(r"<m:oMath>", document_xml)), 1)
        for label in LEGACY_LABELS:
            self.assertNotIn(label, document_xml)

    def test_word_omml_count_matches_equation_components(self) -> None:
        from video_study.render_v31 import count_word_omml
        document = _v31_document()
        equations = component_statistics(document)["equations"]
        self.assertEqual(equations, 1)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "document-v3.json"
            source.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
            word = root / "lesson.docx"
            render_docx_v31(source, word, project_root=root, cancel_check=lambda: False)
            self.assertEqual(count_word_omml(word), equations)


class NativePdfTests(unittest.TestCase):
    def test_pdf_builtin_fallback_renders_pages(self) -> None:
        from pypdf import PdfReader
        document = _v31_document()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "lesson.pdf"
            render_pdf_fallback_v31(document, output, cancel_check=lambda: False)
            self.assertGreater(output.stat().st_size, 0)
            reader = PdfReader(str(output))
            self.assertGreaterEqual(len(reader.pages), 1)


class ProductionImageChainTests(unittest.TestCase):
    def test_adapter_inserts_workspace_image_in_markdown_word_and_fallback_pdf(self) -> None:
        from pypdf import PdfReader

        document = _v31_document()
        document["components"].append(make_component(
            "image", component_id="ch1.img", semantic_role="image",
            visual_id="ve_001", role="explain", caption="生产链路图注", alt_text="板书截图",
            source_timestamp=10.0, source_refs={"segment_ids": ["seg_00001"]},
        ))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video_root = root / "workspace" / "lesson"
            knowledge = video_root / "knowledge"
            image_path = video_root / "images" / "candidates" / "candidate_00001.jpg"
            knowledge.mkdir(parents=True)
            image_path.parent.mkdir(parents=True)
            Image.new("RGB", (320, 180), color=(30, 120, 180)).save(image_path, format="JPEG")
            source = knowledge / "document-v3.json"
            source.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
            (knowledge / "visual-evidence.json").write_text(json.dumps({
                "visual_evidence": [{
                    "evidence_id": "ve_001", "image_path": str(image_path),
                    "width": 320, "height": 180, "source_timestamp": 10.0,
                }],
            }, ensure_ascii=False), encoding="utf-8")

            adapter = DocumentAdapterV31(root)
            markdown = root / "output" / "lesson.md"
            word = root / "output" / "lesson.docx"
            pdf = root / "output" / "lesson.pdf"
            adapter.render_markdown(document, markdown, source_document=source)
            adapter.render_word(source, word, cancel_check=lambda: False)
            adapter.render_pdf(
                document, root / "missing.docx", pdf,
                source_document=source, cancel_check=lambda: False,
            )

            markdown_text = markdown.read_text(encoding="utf-8")
            self.assertIn("workspace/lesson/images/candidates/candidate_00001.jpg", markdown_text)
            self.assertNotIn("](ve_001)", markdown_text)
            with zipfile.ZipFile(word) as archive:
                self.assertTrue(any(name.startswith("word/media/") for name in archive.namelist()))
            reader = PdfReader(str(pdf))
            image_count = 0
            for page in reader.pages:
                resources = page.get("/Resources", {})
                xobjects = resources.get("/XObject", {}) if resources else {}
                for value in xobjects.values():
                    if value.get_object().get("/Subtype") == "/Image":
                        image_count += 1
            self.assertGreaterEqual(image_count, 1)


class NativeContractTests(unittest.TestCase):
    def test_legacy_v2_document_is_rejected_by_v31_renderer(self) -> None:
        document = {"schema_version": 2, "contract_version": "document-v2", "sections": []}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "lesson.md"
            with self.assertRaises(ValueError):
                render_markdown_v31(document, output, project_root=Path(directory))

    def test_unknown_component_type_is_rejected_by_builder(self) -> None:
        with self.assertRaisesRegex(ValueError, "未知"):
            make_component("arbitrary_xml", component_id="x", semantic_role="x")


if __name__ == "__main__":
    unittest.main()
