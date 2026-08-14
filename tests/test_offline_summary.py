from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from video_study.summarize import (
    _normalize_document,
    _validate_qwen_payload,
    build_cloud_source,
    build_cloud_source_blocks,
    build_document,
    merge_transcript_segments,
)
from video_study.render import render_markdown


class OfflineSummaryTests(unittest.TestCase):
    def test_fragmented_asr_is_merged_and_reduced_to_traceable_points(self) -> None:
        texts = [
            "同学们今天开始上课",
            "心房颤动是常见的心律失常",
            "诊断重点包括心电图证据",
            "非常好我们继续",
            "治疗需要结合患者风险分层",
            "抗凝的目的是降低卒中风险",
            "同学们有没有问题",
            "所以需要定期复评出血风险",
        ]
        starts = [0, 8, 20, 35, 310, 324, 340, 355]
        segments = [{
            "segment_id": f"seg_{index:05d}",
            "start_seconds": float(start),
            "end_seconds": float(start + 6),
            "text": text,
        } for index, (text, start) in enumerate(zip(texts, starts), start=1)]
        transcript = {"segments": segments}
        manifest = {
            "video_id": "medical-demo",
            "title": "房颤教学",
            "source_path": "Resource/medical.mp4",
            "duration_seconds": 400.0,
        }
        frames = {"frames": [{
            "image_id": "frame_001",
            "timestamp_seconds": 330.0,
            "timestamp_label": "00:05:30",
            "path": "frame.jpg",
            "caption": "关键画面",
        }]}

        with tempfile.TemporaryDirectory() as temp_dir:
            document = build_document(
                manifest,
                transcript,
                frames,
                Path(temp_dir) / "document.json",
                {"enabled": False},
                {
                    "source_link_base": "video-study://play",
                    "offline_section_seconds": 300,
                    "offline_points_per_section": 2,
                },
            )

        points = [point for section in document["sections"] for point in section["knowledge_points"]]
        self.assertEqual(document["mode"], "offline_extract")
        self.assertEqual(len(document["sections"]), 2)
        self.assertLess(len(points), len(segments))
        self.assertTrue(any("心房颤动" in point["statement"] for point in points))
        self.assertTrue(all(point["source_segment_ids"] for point in points))
        self.assertEqual(document["sections"][0]["figures"], [])
        self.assertEqual(document["sections"][1]["figures"][0]["image_id"], "frame_001")

    def test_merge_preserves_full_time_range_and_source_ids(self) -> None:
        rows = [{
            "segment_id": f"seg_{index:05d}",
            "start_seconds": float(index),
            "end_seconds": float(index + 1),
            "text": text,
        } for index, text in enumerate(("第一句", "第二句", "第三句"), start=1)]

        merged = merge_transcript_segments(rows)

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["start_seconds"], 1.0)
        self.assertEqual(merged[0]["end_seconds"], 4.0)
        self.assertEqual(merged[0]["source_segment_ids"], ["seg_00001", "seg_00002", "seg_00003"])

    def test_cloud_source_compresses_fragments_and_keeps_reference_boundaries(self) -> None:
        rows = [{
            "segment_id": f"seg_{index:05d}",
            "start_seconds": float(index),
            "end_seconds": float(index + 1),
            "text": "这是连续讲解内容",
        } for index in range(1, 21)]

        source = build_cloud_source({"segments": rows})

        self.assertLess(len(source.splitlines()), len(rows))
        self.assertIn("block_0001", source)
        self.assertIn("这是连续讲解内容", source)

        _, source_blocks = build_cloud_source_blocks({"segments": rows})
        self.assertEqual(source_blocks["block_0001"][0], "seg_00001")
        self.assertGreater(len(source_blocks["block_0001"]), 1)

    def test_cloud_payload_requires_material_structure_and_sources(self) -> None:
        valid = {
            "document_title": "视频内容方法导读",
            "overview": "本视频介绍一套方法的核心定义、应用主线以及学习时需要关注的关键关系。",
            "sections": [{
                "title": "核心方法",
                "summary": "说明方法的定义与目标。",
                "knowledge_points": [{
                    "statement": "方法的核心目标",
                    "explanation": "通过结构化观察减少主观判断。",
                    "source_block_ids": ["block_0001"],
                }],
            }],
        }
        _validate_qwen_payload(valid, {"block_0001": ["seg_00001"]})
        invalid = {**valid, "overview": ""}
        with self.assertRaisesRegex(ValueError, "overview"):
            _validate_qwen_payload(invalid)

        guessed = {
            **valid,
            "sections": [{
                **valid["sections"][0],
                "summary": "目标是实现持续盈利（疑似原词）常态化。",
            }],
        }
        with self.assertRaisesRegex(ValueError, "括号猜词"):
            _validate_qwen_payload(guessed, {"block_0001": ["seg_00001"]})

    def test_figures_are_linked_to_nearest_knowledge_point_with_conservative_caption(self) -> None:
        manifest = {"video_id": "demo", "title": "课程", "source_path": "demo.mp4", "duration_seconds": 120}
        transcript = {"segments": [
            {"segment_id": "s1", "start_seconds": 10, "end_seconds": 20, "text": "概念定义"},
            {"segment_id": "s2", "start_seconds": 80, "end_seconds": 90, "text": "应用方法"},
        ]}
        sections = [{"title": "课程内容", "summary": "摘要", "knowledge_points": [
            {"statement": "核心概念", "explanation": "完整解释概念。", "source_segment_ids": ["s1"]},
            {"statement": "应用方法", "explanation": "完整解释方法。", "source_segment_ids": ["s2"]},
        ]}]
        frames = {"frames": [{
            "image_id": "frame_001", "timestamp_seconds": 84, "timestamp_label": "00:01:24",
            "path": "frame.jpg", "caption": "旧图注",
        }]}
        document = _normalize_document(manifest, transcript, frames, sections, "video-study://play", "cloud_summary", "足够长的课程内容导览。")
        first, second = document["sections"][0]["knowledge_points"]
        self.assertEqual(first["figures"], [])
        self.assertEqual(second["figures"][0]["related_point"], "应用方法")
        self.assertIn("讲解同期", second["figures"][0]["caption"])
        self.assertEqual(document["quality"]["figures_linked_to_points"], 1)

    def test_long_course_drops_first_minute_desktop_frame_from_point_figures(self) -> None:
        manifest = {"video_id": "long", "title": "长课程", "source_path": "long.mp4", "duration_seconds": 3600}
        transcript = {"segments": [{"segment_id": "s1", "start_seconds": 10, "end_seconds": 80, "text": "课程导入"}]}
        sections = [{"title": "导入", "summary": "摘要", "knowledge_points": [{
            "statement": "课程导入", "explanation": "讲解课程结构。", "source_segment_ids": ["s1"],
        }]}]
        frames = {"frames": [
            {"image_id": "desktop", "timestamp_seconds": 26, "timestamp_label": "00:00:26", "path": "desktop.jpg", "caption": "桌面"},
            {"image_id": "lesson", "timestamp_seconds": 70, "timestamp_label": "00:01:10", "path": "lesson.jpg", "caption": "课件"},
        ]}
        document = _normalize_document(manifest, transcript, frames, sections, "video-study://play", "cloud_summary", "足够长的课程内容导览。")
        figures = document["sections"][0]["knowledge_points"][0]["figures"]
        self.assertEqual([figure["image_id"] for figure in figures], ["lesson"])

    def test_markdown_renders_course_note_structure_and_review(self) -> None:
        document = {
            "metadata": {"title": "课程", "duration_label": "00:02:00", "video_id": "demo"},
            "mode": "cloud_summary", "notice": "请核对来源。", "overview": "课程内容导览。",
            "learning_objectives": ["掌握核心方法"], "figures": [], "transcript": [],
            "sections": [{"title": "方法", "summary": "章节摘要", "figures": [], "knowledge_points": [{
                "statement": "核心方法", "explanation": "完整解释。", "details": ["推导细节"],
                "steps": ["第一步"], "examples": ["课程案例"], "conditions": ["适用条件"],
                "pitfalls": ["常见错误"], "editorial_note": "把分散说明整理为连续步骤。",
                "review_tip": "先判断条件，再执行步骤。", "figures": [],
                "source_label": "00:10–00:20", "source_url": "video-study://play/demo?t=10",
            }]}],
            "review": {"knowledge_thread": "定义到应用。", "checklist": ["检查条件"], "open_questions": ["待回看案例"]},
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "notes.md"
            render_markdown(document, output, False)
            text = output.read_text(encoding="utf-8")
        self.assertIn("## 学习目标", text)
        self.assertIn("**课程案例**", text)
        self.assertIn("**整理说明**", text)
        self.assertIn("## 课程复习", text)


if __name__ == "__main__":
    unittest.main()
