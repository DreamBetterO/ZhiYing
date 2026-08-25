"""CP61-2 责任测试：QualityReport v2 与 PageAuditReport v1。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from zhiying.editorial.document import build_v31_document, make_component
from zhiying.editorial.intent import compile_editorial_policy
from zhiying.editorial.quality import (
    audit_document_v31,
    audit_render_outputs,
    build_page_audit_report,
)
from zhiying.knowledge.editorial import brief_from_text
from zhiying.documents.render_v31 import render_markdown_v31


def _document(**overrides) -> dict:
    return build_v31_document(
        metadata={"video_id": "lesson", "document_title": "课程"},
        components=[
            make_component("heading", component_id="h1", semantic_role="heading", text="第一章", level=2),
            make_component("paragraph", component_id="p1", semantic_role="paragraph", text="正文", source_refs={"segment_ids": ["seg_00001"]}),
            make_component("equation", component_id="e1", semantic_role="equation", latex="\\int x dx", source_refs={"segment_ids": ["seg_00002"]}),
        ],
        **overrides,
    )


class QualityReportTests(unittest.TestCase):
    def test_valid_document_passes_quality_audit(self) -> None:
        report = audit_document_v31(_document())
        self.assertEqual(report["status"], "valid")
        self.assertEqual(report["math"]["equation_components"], 1)
        self.assertEqual(report["evidence"]["source_reference_components"], 0)

    def test_missing_source_refs_is_error(self) -> None:
        document = _document()
        document["components"][1] = make_component("paragraph", component_id="p1", semantic_role="paragraph", text="无来源正文")
        report = audit_document_v31(document)
        self.assertEqual(report["status"], "invalid")
        self.assertTrue(any(row["code"] == "EVIDENCE_NO_SOURCE" for row in report["issues"]))

    def test_forbidden_policy_hits_are_reported(self) -> None:
        policy = compile_editorial_policy(brief_from_text("不要内容导览、不要学习目标、不要课程复习"))
        document = _document()
        document["components"].insert(0, make_component("container", component_id="overview_1", semantic_role="overview", title="导览"))
        report = audit_document_v31(document, policy=policy)
        self.assertEqual(report["status"], "invalid")
        self.assertIn("overview", report["intent"]["forbidden_hits"])
        self.assertTrue(any(row["code"] == "INTENT_FORBIDDEN_HIT" for row in report["issues"]))

    def test_image_requires_role_caption_alt(self) -> None:
        document = _document()
        document["components"].append(make_component(
            "image", component_id="img1", semantic_role="image", visual_id="ve_1",
            caption="图注", alt_text="alt", source_timestamp=1.0,
        ))  # 缺 role
        report = audit_document_v31(document)
        self.assertEqual(report["status"], "invalid")
        self.assertTrue(any(row["code"] == "VISUAL_FIELD_MISSING" for row in report["issues"]))

    def test_content_far_below_blueprint_target_is_invalid(self) -> None:
        document = _document(provenance={"target_chars": 1000, "available_content_chars": 1000})
        report = audit_document_v31(document)
        self.assertEqual(report["status"], "invalid")
        self.assertTrue(any(row["code"] == "CONTENT_UNDER_TARGET" for row in report["issues"]))
        self.assertLess(report["content"]["actual_chars"], report["content"]["minimum_chars"])

    def test_render_audit_detects_omml_mismatch_and_path_leak(self) -> None:
        document = _document()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            markdown = root / "lesson.md"
            render_markdown_v31(document, markdown, project_root=root)
            markdown.write_text(markdown.read_text(encoding="utf-8") + "\n![x](D:/leak.png)", encoding="utf-8")
            docx = root / "lesson.docx"
            docx.write_bytes(b"not a docx")
            pdf = root / "lesson.pdf"
            pdf.write_bytes(b"pdf")
            report = audit_render_outputs(document, markdown=markdown, docx=docx, pdf=pdf, pdf_mode="built_in_v31")
        self.assertEqual(report["status"], "invalid")
        codes = {row["code"] for row in report["issues"]}
        self.assertIn("MARKDOWN_ABSOLUTE_PATH", codes)
        self.assertIn("MATH_OMML_MISMATCH", codes)

    def test_render_audit_passes_when_omml_matches(self) -> None:
        document = _document()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            markdown = root / "lesson.md"
            render_markdown_v31(document, markdown, project_root=root)
            report = audit_render_outputs(document, markdown=markdown, pdf_mode=None)
        self.assertEqual(report["statistics"]["equation_components"], 1)
        self.assertEqual(report["statistics"]["markdown_equation_blocks"], 1)


class PageAuditTests(unittest.TestCase):
    def test_page_audit_statistics_and_orphan_warning(self) -> None:
        document = _document()
        document["components"].append(make_component(
            "container", component_id="chapter_002", semantic_role="chapter", title="第二章", children=[],
        ))
        document["components"][0]["children"] = document["components"][0].get("children", [])
        report = build_page_audit_report(document)
        self.assertEqual(report["schema_version"], 1)
        self.assertIn("heading_components", report["statistics"])
        self.assertEqual(report["statistics"]["equation_components"], 1)

    def test_fixed_numbering_residue_is_error(self) -> None:
        document = _document()
        document["components"][0] = make_component("heading", component_id="h1", semantic_role="heading", text="01 · 第一章", level=2)
        report = build_page_audit_report(document)
        self.assertEqual(report["status"], "invalid")
        self.assertTrue(any(row["code"] == "PAGE_FIXED_NUMBERING" for row in report["issues"]))


if __name__ == "__main__":
    unittest.main()
