"""渲染链路集成测试：验证 content_blocks 在 Markdown/PDF 中的渲染。"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from zhiying.knowledge.adapter import v1_to_v2
from zhiying.render import render_markdown, render_pdf_fallback


class ContentBlocksRenderTests(unittest.TestCase):
    def _make_document(self, with_blocks: bool = True) -> dict:
        point = {
            "statement": "黑K判定规则",
            "explanation": "与M5同价不算黑K",
            "details": ["补充"],
            "steps": [],
            "examples": [],
            "conditions": [],
            "pitfalls": [],
            "editorial_note": "",
            "review_tip": "",
            "source_segment_ids": ["seg_001"],
            "start_seconds": 10.0,
            "end_seconds": 20.0,
            "source_label": "00:00:10–00:00:20",
            "source_url": "video-study://play/test-001?t=10",
            "figures": [],
        }
        if with_blocks:
            point["content_blocks"] = [
                {"block_id": "b1", "type": "paragraph", "origin": "source_backed", "text": "黑K是与M5同价的K线"},
                {"block_id": "b2", "type": "rule_list", "origin": "source_backed", "items": ["同价不算黑K", "跳空缺口例外"]},
                {"block_id": "b3", "type": "understanding_tip", "origin": "model_aid", "text": "记住同价即不算"},
            ]
        return {
            "schema_version": 1,
            "generated_at": "2026-01-01T00:00:00",
            "mode": "cloud_summary",
            "metadata": {
                "video_id": "test-001",
                "title": "测试",
                "document_title": "测试课程",
                "source_video": "test.mp4",
                "duration_seconds": 120,
                "duration_label": "00:02:00",
            },
            "overview": "测试导览",
            "learning_objectives": [],
            "sections": [{
                "title": "第一章",
                "summary": "摘要",
                "start_seconds": 10.0,
                "end_seconds": 20.0,
                "knowledge_points": [point],
                "figures": [],
            }],
            "figures": [],
            "transcript": [],
            "notice": "测试声明",
            "review": {},
            "quality": {},
        }

    def test_markdown_renders_content_blocks(self) -> None:
        doc = self._make_document(with_blocks=True)
        with tempfile.NamedTemporaryFile(suffix=".md", delete=False, mode="w", encoding="utf-8") as f:
            md_path = Path(f.name)
        render_markdown(doc, md_path)
        content = md_path.read_text(encoding="utf-8")
        md_path.unlink()
        self.assertIn("黑K是与M5同价的K线", content)
        self.assertIn("**规则**", content)
        self.assertIn("同价不算黑K", content)
        self.assertIn("理解提示", content)
        # 不应出现旧字段标签
        self.assertNotIn("**补充细节**", content)

    def test_v1_and_v2_render_same_canonical_block_order_and_links(self) -> None:
        v1 = self._make_document(with_blocks=True)
        v2 = v1_to_v2(v1)
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = root / "v1.md"
            second = root / "v2.md"
            render_markdown(v1, first, False)
            render_markdown(v2, second, False)
            self.assertEqual(first.read_text(encoding="utf-8"), second.read_text(encoding="utf-8"))

    def test_markdown_falls_back_without_blocks(self) -> None:
        doc = self._make_document(with_blocks=False)
        with tempfile.NamedTemporaryFile(suffix=".md", delete=False, mode="w", encoding="utf-8") as f:
            md_path = Path(f.name)
        render_markdown(doc, md_path)
        content = md_path.read_text(encoding="utf-8")
        md_path.unlink()
        self.assertIn("与M5同价不算黑K", content)
        self.assertIn("**补充细节**", content)

    def test_list_block_with_text_variant_is_not_dropped(self) -> None:
        doc = self._make_document(with_blocks=True)
        point = doc["sections"][0]["knowledge_points"][0]
        point["content_blocks"] = [{
            "block_id": "b1",
            "type": "rule_list",
            "origin": "audio_backed",
            "text": "有反串一定有正串",
        }]
        with tempfile.NamedTemporaryFile(suffix=".md", delete=False, mode="w", encoding="utf-8") as f:
            md_path = Path(f.name)
        render_markdown(doc, md_path)
        content = md_path.read_text(encoding="utf-8")
        md_path.unlink()
        self.assertIn("**规则**", content)
        self.assertIn("有反串一定有正串", content)

    def test_markdown_renders_v211_visual_group(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            image_path = root / "frame.jpg"
            Image.new("RGB", (320, 180), "white").save(image_path)
            doc = self._make_document(with_blocks=True)
            point = doc["sections"][0]["knowledge_points"][0]
            point["figures"] = [{
                "image_id": "candidate_00043",
                "binding_id": "ve_001",
                "path": str(image_path),
                "caption": "图 1 共同区间",
                "timestamp_seconds": 608.0,
                "timestamp_label": "00:10:08",
                "source_url": "video-study://play/test-001?t=608",
            }]
            point["content_blocks"] = [{
                "block_id": "vg1", "type": "visual_group", "binding_id": "ve_001",
                "lead_in": "看图重点：先看共同区间",
                "caption": "图 1 共同区间",
                "takeaway": "这张图说明重叠是区间",
            }]
            md_path = root / "result.md"
            render_markdown(doc, md_path)
            content = md_path.read_text(encoding="utf-8")
            self.assertIn("**看图重点**：先看共同区间", content)
            self.assertEqual(content.count("*图 1 共同区间*"), 1)
            self.assertIn("这张图帮助理解", content)
            self.assertIn("查看图片来源 · 00:10:08", content)
            self.assertIn("video-study://play/test-001?t=608", content)

    def test_pdf_fallback_renders_content_blocks(self) -> None:
        doc = self._make_document(with_blocks=True)
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            pdf_path = Path(f.name)
        render_pdf_fallback(doc, pdf_path)
        self.assertTrue(pdf_path.exists())
        self.assertGreater(pdf_path.stat().st_size, 1000)
        pdf_path.unlink()

    def test_markdown_renders_comparison_pair_as_one_group(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = root / "before.jpg"
            second = root / "after.jpg"
            Image.new("RGB", (320, 180), "white").save(first)
            Image.new("RGB", (320, 180), "gray").save(second)
            doc = self._make_document(with_blocks=True)
            point = doc["sections"][0]["knowledge_points"][0]
            point["figures"] = [
                {"binding_id": "ve_001", "path": str(first), "caption": "操作前",
                 "timestamp_seconds": 10.0, "timestamp_label": "00:00:10",
                 "source_url": "video-study://play/test-001?t=10"},
                {"binding_id": "ve_002", "path": str(second), "caption": "操作后",
                 "timestamp_seconds": 20.0, "timestamp_label": "00:00:20",
                 "source_url": "video-study://play/test-001?t=20"},
            ]
            point["content_blocks"] = [{
                "block_id": "vg_pair", "type": "visual_group", "binding_id": "ve_001",
                "binding_ids": ["ve_001", "ve_002"], "sequence_mode": "comparison_pair",
                "lead_in": "看图重点：比较前后状态", "takeaway": "前后状态发生变化",
            }]
            md_path = root / "pair.md"
            render_markdown(doc, md_path)
            content = md_path.read_text(encoding="utf-8")
            self.assertEqual(content.count("**看图重点**：比较前后状态"), 1)
            self.assertEqual(content.count("这组图帮助理解"), 1)
            self.assertEqual(content.count("查看图片来源"), 2)

    def test_pdf_fallback_renders_visual_group(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            image_path = root / "frame.jpg"
            Image.new("RGB", (640, 360), "white").save(image_path)
            doc = self._make_document(with_blocks=True)
            point = doc["sections"][0]["knowledge_points"][0]
            point["figures"] = [{
                "image_id": "candidate_00043", "binding_id": "ve_001",
                "path": str(image_path), "caption": "共同区间",
                "timestamp_seconds": 608.0, "timestamp_label": "00:10:08",
                "source_url": "video-study://play/test-001?t=608",
            }]
            point["content_blocks"] = [{
                "block_id": "vg1", "type": "visual_group", "binding_id": "ve_001",
                "lead_in": "看图重点：先看共同区间", "caption": "共同区间",
                "takeaway": "这张图说明重叠是区间",
            }]
            pdf_path = root / "result.pdf"
            render_pdf_fallback(doc, pdf_path)
            self.assertGreater(pdf_path.stat().st_size, 1000)


if __name__ == "__main__":
    unittest.main()
