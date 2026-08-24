from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from zhiying.knowledge.adapter import (
    _reader_open_questions,
    units_to_document,
    v1_to_v2,
)
from zhiying.knowledge.schema import KnowledgeUnit, VisualBinding, VisualEvidence, LessonPlan, ChapterPlan, UnitPlan


class AdapterTests(unittest.TestCase):
    def test_v1_to_v2_is_read_only_and_removes_persisted_body_copies(self) -> None:
        unit = KnowledgeUnit(
            unit_id="u1", title="规则", importance="core",
            definition_or_conclusion="必须先确认方向",
            evidence_refs=[{"segment_ids": ["seg_00001"]}],
            content_blocks=[{
                "block_id": "b1", "type": "rule_list", "origin": "audio_backed",
                "items": ["必须先确认方向"],
            }],
        )
        v2 = units_to_document(
            [unit], self._make_manifest(), self._make_transcript(), self._make_frames(),
            "video-study://play",
        )
        point = v2["sections"][0]["knowledge_points"][0]
        self.assertEqual(v2["schema_version"], 2)
        for field in ("explanation", "details", "steps", "examples", "conditions", "pitfalls"):
            self.assertNotIn(field, point)
        self.assertEqual(point["source_refs"]["segment_ids"], ["seg_00001"])
        self.assertTrue(point["source_refs"]["url"].startswith("video-study://play/"))

    def test_runtime_failures_do_not_leak_into_reader_open_questions(self) -> None:
        self.assertEqual(
            _reader_open_questions([
                "chapter_002: 云端请求预算已用尽（5/5）",
                "讲者未解释第 7 根 K 线的例外情况",
            ]),
            ["讲者未解释第 7 根 K 线的例外情况"],
        )

    def test_adapter_normalizes_list_block_text_to_items(self) -> None:
        unit = KnowledgeUnit(
            unit_id="u1",
            type="rule",
            title="反串与正串",
            importance="core",
            definition_or_conclusion="有反串一定有正串",
            evidence_refs=[{"segment_ids": ["seg_00001"]}],
            content_blocks=[{
                "block_id": "content_001",
                "type": "rule_list",
                "origin": "audio_backed",
                "text": "有反串一定有正串",
            }],
        )
        document = units_to_document(
            [unit],
            self._make_manifest(),
            self._make_transcript(),
            self._make_frames(),
            "video-study://play",
        )
        block = document["sections"][0]["knowledge_points"][0]["content_blocks"][0]
        self.assertEqual(block["items"], ["有反串一定有正串"])
        self.assertNotIn("text", block)

    def _make_manifest(self) -> dict:
        return {
            "video_id": "test-001",
            "title": "测试课程",
            "source_path": "test.mp4",
            "duration_seconds": 120.0,
        }

    def _make_transcript(self) -> dict:
        return {
            "segments": [
                {"segment_id": "seg_00001", "start_seconds": 10.0, "end_seconds": 20.0, "text": "黑K的判定规则"},
                {"segment_id": "seg_00002", "start_seconds": 30.0, "end_seconds": 40.0, "text": "中枢的定义"},
                {"segment_id": "seg_00003", "start_seconds": 50.0, "end_seconds": 60.0, "text": "例外情况"},
            ]
        }

    def _make_frames(self) -> dict:
        return {"frames": [
            {"image_id": "f1", "timestamp_seconds": 35.0, "timestamp_label": "00:00:35", "path": "f1.jpg", "caption": "旧图注"},
        ]}

    def test_units_to_document_produces_valid_structure(self) -> None:
        units = [
            KnowledgeUnit(
                unit_id="u1", type="rule", title="黑K判定", importance="core",
                definition_or_conclusion="与M5同价不算黑K",
                rules=[{"condition": "K线最低价等于M5", "conclusion": "不成立"}],
                evidence_refs=[{"segment_ids": ["seg_00001"]}],
            ),
            KnowledgeUnit(
                unit_id="u2", type="concept", title="中枢定义", importance="core",
                definition_or_conclusion="中枢是线段分析的核心概念",
                evidence_refs=[{"segment_ids": ["seg_00002"]}],
            ),
        ]
        doc = units_to_document(
            units, self._make_manifest(), self._make_transcript(), self._make_frames(),
            "video-study://play",
        )
        self.assertEqual(doc["schema_version"], 2)
        self.assertEqual(len(doc["sections"]), 1)
        self.assertTrue(all(s["knowledge_points"] for s in doc["sections"]))
        point = doc["sections"][0]["knowledge_points"][0]
        self.assertTrue(point["source_refs"]["url"].startswith("video-study://play/test-001?t="))
        self.assertEqual(point["source_refs"]["segment_ids"], ["seg_00001"])

    def test_units_to_document_can_persist_schema_v2(self) -> None:
        unit = KnowledgeUnit(
            unit_id="u1", title="中枢定义", importance="core",
            definition_or_conclusion="中枢是核心概念",
            evidence_refs=[{"segment_ids": ["seg_00002"]}],
            content_blocks=[{"block_id": "b1", "type": "paragraph", "origin": "audio_backed", "text": "中枢是核心概念"}],
        )
        document = units_to_document(
            [unit], self._make_manifest(), self._make_transcript(), self._make_frames(),
            "video-study://play",
        )
        point = document["sections"][0]["knowledge_points"][0]
        self.assertEqual(document["schema_version"], 2)
        self.assertNotIn("explanation", point)
        self.assertEqual(point["content_blocks"][0]["text"], "中枢是核心概念")

    def test_no_figures_linked_without_bindings(self) -> None:
        """无显式绑定时图片不关联到知识点。"""
        units = [
            KnowledgeUnit(
                unit_id="u1", type="concept", title="中枢", importance="core",
                definition_or_conclusion="中枢定义",
                evidence_refs=[{"segment_ids": ["seg_00002"]}],
            ),
        ]
        doc = units_to_document(
            units, self._make_manifest(), self._make_transcript(), self._make_frames(),
            "video-study://play",
        )
        point = doc["sections"][0]["knowledge_points"][0]
        self.assertEqual(len(point["figures"]), 0)

    def test_figures_linked_by_explicit_binding(self) -> None:
        """有显式绑定时图片正确关联到知识点。"""
        units = [
            KnowledgeUnit(
                unit_id="u1", type="concept", title="中枢", importance="core",
                definition_or_conclusion="中枢定义",
                evidence_refs=[{"segment_ids": ["seg_00002"]}],
                plan_id="plan_001",
            ),
        ]
        bindings = [VisualBinding(
            frame_id="f1",
            unit_id="plan_001",
            relation="illustration",
            reader_focus="观察中枢的结构",
            confidence=0.85,
            basis=["ocr_overlap", "nearby_explanation"],
            decision="bind",
        )]
        doc = units_to_document(
            units, self._make_manifest(), self._make_transcript(), self._make_frames(),
            "video-study://play",
            visual_bindings=bindings,
        )
        point = doc["sections"][0]["knowledge_points"][0]
        self.assertEqual(len(point["figures"]), 1)
        self.assertIn("观察重点", point["figures"][0]["caption"])
        self.assertEqual(point["figures"][0]["reader_focus"], "观察中枢的结构")

    def test_binding_none_not_linked(self) -> None:
        """decision=none 的绑定不关联图片。"""
        units = [
            KnowledgeUnit(
                unit_id="u1", type="concept", title="中枢", importance="core",
                definition_or_conclusion="中枢定义",
                evidence_refs=[{"segment_ids": ["seg_00002"]}],
                plan_id="plan_001",
            ),
        ]
        bindings = [VisualBinding(
            frame_id="f1",
            unit_id="plan_001",
            relation="illustration",
            confidence=0.2,
            basis=["time"],
            decision="none",
        )]
        doc = units_to_document(
            units, self._make_manifest(), self._make_transcript(), self._make_frames(),
            "video-study://play",
            visual_bindings=bindings,
        )
        point = doc["sections"][0]["knowledge_points"][0]
        self.assertEqual(len(point["figures"]), 0)

    def test_visual_evidence_links_by_evidence_id(self) -> None:
        unit = KnowledgeUnit(
            unit_id="u1", type="rule", title="图表规则", importance="core",
            definition_or_conclusion="看图判断",
            evidence_refs=[{"segment_ids": ["seg_00002"]}],
            plan_id="plan_001",
            detail_level="standard",
            content_blocks=[
                {"block_id": "b1", "type": "visual_lead_in", "text": "看图重点：看关系"},
                {"block_id": "b2", "type": "figure", "binding_id": "ve_001"},
                {"block_id": "b3", "type": "visual_takeaway", "text": "图帮助理解关系"},
            ],
        )
        evidence = [VisualEvidence(
            evidence_id="ve_001",
            question_id="vq_001_01",
            image_path="D:\\tmp\\frame.jpg",
            timestamp=35.0,
            matched_knowledge_point_id="plan_001",
            why_useful="看关系",
            suggested_caption="图表规则图",
            explanation_for_reader="图帮助理解关系",
            frame_id="candidate_00004",
            relevance_score=0.5,
            ocr_text="图表规则",
            visible_evidence=["OCR 命中：图表规则"],
            scene_cluster_id="scene_00001",
            dedup_group_id="scene_00001",
            decision="select",
        )]
        doc = units_to_document(
            [unit], self._make_manifest(), self._make_transcript(), {"frames": []},
            "video-study://play", visual_evidence=evidence,
        )
        point = doc["sections"][0]["knowledge_points"][0]
        self.assertEqual(point["figures"][0]["binding_id"], "ve_001")
        self.assertTrue(point["figures"][0]["source_url"].startswith("video-study://play/test-001?t=35"))
        self.assertEqual([block["type"] for block in point["content_blocks"]], ["visual_group"])
        self.assertEqual(doc["visual_evidence"][0]["evidence_id"], "ve_001")
        self.assertEqual(doc["visual_source"], "visual_evidence")
        self.assertEqual(doc["visual_source_version"], "3.0")

    def test_visual_evidence_disables_legacy_bindings_for_whole_task(self) -> None:
        unit = KnowledgeUnit(
            unit_id="u1", type="concept", title="中枢", importance="core",
            definition_or_conclusion="中枢定义",
            evidence_refs=[{"segment_ids": ["seg_00002"]}],
            plan_id="plan_001",
            content_blocks=[
                {"block_id": "old-lead", "type": "visual_lead_in", "text": "如下图"},
                {"block_id": "old-figure", "type": "figure", "binding_id": "f1"},
                {"block_id": "body", "type": "paragraph", "text": "正文"},
            ],
        )
        legacy = [VisualBinding(
            frame_id="f1", unit_id="plan_001", relation="illustration",
            reader_focus="旧绑定", confidence=0.9, basis=["ocr"], decision="bind",
        )]
        no_match = [VisualEvidence(
            evidence_id="ve_001", question_id="vq_001_01",
            matched_knowledge_point_id="plan_001", decision="no_match",
            match_reason="没有像素证据",
        )]
        doc = units_to_document(
            [unit], self._make_manifest(), self._make_transcript(), self._make_frames(),
            "video-study://play", visual_bindings=legacy, visual_evidence=no_match,
        )
        point = doc["sections"][0]["knowledge_points"][0]
        self.assertEqual(point["figures"], [])
        self.assertEqual([block["type"] for block in point["content_blocks"]], ["paragraph"])
        self.assertEqual(doc["visual_source"], "visual_evidence")

    def test_comparison_evidence_stays_in_one_visual_group(self) -> None:
        unit = KnowledgeUnit(
            unit_id="u1", type="comparison", title="状态对比", importance="core",
            definition_or_conclusion="对比前后状态",
            evidence_refs=[{"segment_ids": ["seg_00002"]}],
            plan_id="plan_001",
            content_blocks=[{"block_id": "body", "type": "paragraph", "text": "正文"}],
        )
        evidence = [
            VisualEvidence(
                evidence_id=f"ve_00{index}", question_id=f"vq_00{index}",
                image_path=f"D:\\tmp\\frame{index}.jpg", timestamp=35.0 + index,
                matched_knowledge_point_id="plan_001", frame_id=f"candidate_0000{index}",
                why_useful="比较前后状态", suggested_caption=f"状态 {index}",
                explanation_for_reader=f"状态 {index} 的可见差异",
                visible_evidence=[f"可见状态 {index}"], criteria_met=["可见状态"],
                visual_role="compare", sequence_mode="comparison_pair",
                visual_group_id="vg_plan_001", scene_cluster_id=f"scene_0000{index}",
                decision="select",
            )
            for index in (1, 2)
        ]
        doc = units_to_document(
            [unit], self._make_manifest(), self._make_transcript(), {"frames": []},
            "video-study://play", visual_evidence=evidence,
        )
        point = doc["sections"][0]["knowledge_points"][0]
        groups = [block for block in point["content_blocks"] if block["type"] == "visual_group"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["binding_ids"], ["ve_001", "ve_002"])
        self.assertEqual(len(point["figures"]), 2)

    def test_content_blocks_rendered(self) -> None:
        """有 content_blocks 时旧字段从 blocks 生成。"""
        units = [KnowledgeUnit(
            unit_id="u1", type="rule", title="黑K规则", importance="core",
            definition_or_conclusion="与M5同价不算黑K",
            evidence_refs=[{"segment_ids": ["seg_00001"]}],
            content_blocks=[
                {"block_id": "b1", "type": "paragraph", "origin": "source_backed", "text": "段落"},
                {"block_id": "b2", "type": "rule_list", "origin": "source_backed", "items": ["规则1"]},
                {"block_id": "b3", "type": "steps", "origin": "derived_explanation", "items": ["步骤1"]},
                {"block_id": "b4", "type": "example", "origin": "source_backed", "items": ["案例1"]},
                {"block_id": "b5", "type": "understanding_tip", "origin": "model_aid", "text": "提示"},
            ],
        )]
        doc = units_to_document(
            units, self._make_manifest(), self._make_transcript(), {"frames": []},
            "video-study://play",
        )
        point = doc["sections"][0]["knowledge_points"][0]
        block_types = {block["type"] for block in point["content_blocks"]}
        self.assertTrue({"rule_list", "steps", "example", "understanding_tip"} <= block_types)
        self.assertTrue(point["content_blocks"])

    def test_cloud_info_populates_metadata(self) -> None:
        units = [KnowledgeUnit(
            unit_id="u1", type="concept", title="测试", importance="core",
            definition_or_conclusion="定义",
            evidence_refs=[{"segment_ids": ["seg_00001"]}],
        )]
        cloud_info = {
            "model": "qwen3.7-plus",
            "attempts": [{"model": "qwen3.7-plus", "ok": True}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            "document_title": "黑K判定规则",
            "overview": "本视频讲解黑K的判定方法。",
            "learning_objectives": ["掌握黑K判定"],
            "review": {"knowledge_thread": "主线", "checklist": ["检查同价"], "open_questions": []},
        }
        doc = units_to_document(
            units, self._make_manifest(), self._make_transcript(), {"frames": []},
            "video-study://play", cloud_info=cloud_info,
        )
        self.assertEqual(doc["mode"], "cloud_summary")
        self.assertEqual(doc["metadata"]["document_title"], "黑K判定规则")
        self.assertEqual(doc["overview"], "本视频讲解黑K的判定方法。")
        self.assertEqual(doc["model"], "qwen3.7-plus")
        self.assertIn("检查同价", doc["review"]["checklist"])

    def test_selfcheck_report_included(self) -> None:
        units = [KnowledgeUnit(
            unit_id="u1", type="rule", title="规则", importance="core",
            definition_or_conclusion="定义",
            evidence_refs=[{"segment_ids": ["seg_00001"]}],
        )]
        report = {"passed": True, "errors": 0, "warnings": 1, "stats": {"expand": 1}}
        doc = units_to_document(
            units, self._make_manifest(), self._make_transcript(), {"frames": []},
            "video-study://play", selfcheck_report=report,
        )
        self.assertIn("selfcheck", doc)
        self.assertTrue(doc["selfcheck"]["passed"])

    def test_quality_stats_correct(self) -> None:
        units = [
            KnowledgeUnit(
                unit_id="u1", type="rule", title="规则1", importance="core",
                definition_or_conclusion="定义1",
                evidence_refs=[{"segment_ids": ["seg_00001"]}],
            ),
            KnowledgeUnit(
                unit_id="u2", type="concept", title="概念1", importance="core",
                definition_or_conclusion="定义2",
                evidence_refs=[{"segment_ids": ["seg_00002"]}],
            ),
        ]
        doc = units_to_document(
            units, self._make_manifest(), self._make_transcript(), {"frames": []},
            "video-study://play",
        )
        self.assertEqual(doc["quality"]["knowledge_point_count"], 2)
        self.assertGreater(doc["quality"]["source_segment_coverage"], 0)

    def test_unit_without_valid_segments_skipped(self) -> None:
        units = [
            KnowledgeUnit(
                unit_id="u1", type="concept", title="有效", importance="core",
                definition_or_conclusion="定义",
                evidence_refs=[{"segment_ids": ["seg_00001"]}],
            ),
            KnowledgeUnit(
                unit_id="u2", type="concept", title="无效", importance="core",
                definition_or_conclusion="无来源",
                evidence_refs=[{"segment_ids": ["nonexistent"]}],
            ),
        ]
        doc = units_to_document(
            units, self._make_manifest(), self._make_transcript(), {"frames": []},
            "video-study://play",
        )
        total_points = sum(len(s["knowledge_points"]) for s in doc["sections"])
        self.assertEqual(total_points, 1)

    def test_rule_type_maps_to_fields(self) -> None:
        units = [KnowledgeUnit(
            unit_id="u1", type="rule", title="黑K规则", importance="core",
            definition_or_conclusion="与M5同价不算黑K",
            rules=[{"condition": "同价", "conclusion": "不成立"}],
            exceptions=["跳空缺口例外"],
            pitfalls=["容易混淆收盘价"],
            positive_examples=["案例1"],
            evidence_refs=[{"segment_ids": ["seg_00001"]}],
        )]
        doc = units_to_document(
            units, self._make_manifest(), self._make_transcript(), {"frames": []},
            "video-study://play",
        )
        point = doc["sections"][0]["knowledge_points"][0]
        block_types = {block["type"] for block in point["content_blocks"]}
        self.assertIn("rule_list", block_types)
        self.assertIn("pitfall", block_types)
        self.assertIn("example", block_types)


if __name__ == "__main__":
    unittest.main()
