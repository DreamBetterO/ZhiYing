import unittest
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from video_study.aggregate import _aggregate_source, _validate_aggregate, aggregate_documents
from video_study.config import AppConfig


class AggregateTests(unittest.TestCase):
    def test_source_assigns_stable_ids_and_preserves_video_links(self) -> None:
        documents = [{
            "metadata": {"title": "第一课"}, "overview": "导览",
            "sections": [{"title": "主题", "knowledge_points": [{
                "statement": "定义", "explanation": "详细说明",
                "source_label": "00:01–00:02", "source_url": "video-study://play/a?t=1",
            }]}],
        }, {
            "metadata": {"title": "第二课"}, "overview": "导览",
            "sections": [{"title": "进阶", "knowledge_points": [{
                "statement": "关系", "explanation": "进一步说明",
                "source_label": "00:03–00:04", "source_url": "video-study://play/b?t=3",
            }]}],
        }]
        source, points = _aggregate_source(documents)
        self.assertIn("[point_0001]", source)
        self.assertIn("[point_0002]", source)
        self.assertEqual(points["point_0002"]["links"][0]["url"], "video-study://play/b?t=3")

    def test_payload_rejects_invented_source_point(self) -> None:
        payload = {
            "document_title": "课程合集", "overview": "这是足够长的聚合内容导览，用于说明多视频之间的完整关系。",
            "chapter_title": "统一章节", "chapter_summary": "章节摘要",
            "knowledge_points": [{
                "statement": "知识点", "explanation": "这是聚合后的详细知识点解释。",
                "source_point_ids": ["point_9999"],
            }],
        }
        with self.assertRaises(ValueError):
            _validate_aggregate(payload, {"point_0001"})

    def test_payload_accepts_multiple_logical_sections(self) -> None:
        payload = {
            "document_title": "课程合集", "overview": "这是足够长的聚合内容导览，用于说明多视频之间的完整关系。",
            "sections": [
                {"title": "基础", "summary": "基础摘要", "knowledge_points": [{
                    "statement": "定义", "explanation": "这是基础定义的完整解释。", "source_point_ids": ["point_0001"],
                }]},
                {"title": "应用", "summary": "应用摘要", "knowledge_points": [{
                    "statement": "方法", "explanation": "这是应用方法的完整解释。", "source_point_ids": ["point_0002"],
                }]},
            ],
        }
        _validate_aggregate(payload, {"point_0001", "point_0002"})

    def test_legacy_http_source_is_rewritten_to_local_protocol(self) -> None:
        documents = [{
            "metadata": {"video_id": "课程 一", "title": "第一课"}, "overview": "导览",
            "sections": [{"title": "主题", "knowledge_points": [{
                "statement": "定义", "explanation": "详细说明", "start_seconds": 15,
                "source_label": "00:15–00:20", "source_url": "http://127.0.0.1:8765/watch/old?t=15",
            }]}],
        }]
        _, points = _aggregate_source(documents)
        self.assertTrue(points["point_0001"]["links"][0]["url"].startswith("video-study://play/"))

    def test_aggregate_writes_json_and_markdown_with_multiple_source_links(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            results = []
            for index in (1, 2):
                work = root / "workspace" / f"video-{index}"
                knowledge = work / "knowledge"
                output = root / "output" / f"video-{index}"
                knowledge.mkdir(parents=True); output.mkdir(parents=True)
                document = {
                    "metadata": {"video_id": f"video-{index}", "title": f"第{index}课", "duration_seconds": 60},
                    "overview": "原文导览", "figures": [],
                    "sections": [{"title": "主题", "knowledge_points": [{
                        "statement": f"知识点{index}", "explanation": "来自原始视频的完整解释",
                        "source_label": "00:01–00:02", "source_url": f"video-study://play/video-{index}?t=1",
                    }]}],
                }
                (knowledge / "document.json").write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
                (work / "manifest.json").write_text("{}", encoding="utf-8")
                markdown = output / f"video-{index}.md"; markdown.write_text(f"# 第{index}课", encoding="utf-8")
                results.append({"manifest": work / "manifest.json", "markdown": markdown})
            payload = {
                "document_title": "课程总章", "overview": "这是综合两段视频后形成的完整章节内容导览。",
                "learning_objectives": ["理解两课关系"],
                "sections": [{"title": "统一知识章节", "summary": "综合章节摘要", "knowledge_points": [{
                    "statement": "综合知识点", "explanation": "综合两个视频内容后得到的完整解释。",
                    "details": ["保留课程细节"], "source_point_ids": ["point_0001", "point_0002"],
                }]}],
                "review": {"knowledge_thread": "从定义到关系。", "checklist": ["核对定义"], "open_questions": []},
            }
            client = SimpleNamespace(create_json=lambda **_kwargs: (payload, "model-a", [], {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}))
            config = AppConfig(root, {"paths": {"output_dir": "output"}})
            qwen = {"_runtime_api_key": "temporary", "_runtime_base_url": "https://example.com/v1", "_runtime_models": ["model-a"], "_runtime_max_calls": 1, "budget": {"max_calls_per_video": 1, "max_input_chars": 60000, "max_output_tokens": 1000}}
            with patch("video_study.aggregate.FallbackChatClient", return_value=client), patch("video_study.aggregate.render_docx"), patch("video_study.aggregate.convert_docx_to_pdf", return_value="fallback"):
                result = aggregate_documents(config, results, qwen)
            aggregate = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
            links = aggregate["sections"][0]["knowledge_points"][0]["source_links"]
            self.assertEqual(len(links), 2)
            self.assertEqual(aggregate["learning_objectives"], ["理解两课关系"])
            self.assertEqual(aggregate["sections"][0]["knowledge_points"][0]["details"], ["保留课程细节"])
            markdown_text = Path(result["markdown"]).read_text(encoding="utf-8")
            self.assertIn("video-study://play/video-1", markdown_text)
            self.assertIn("video-study://play/video-2", markdown_text)
