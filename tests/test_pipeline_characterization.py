from __future__ import annotations

import inspect
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image

from video_study import pipeline
from video_study.config import AppConfig
from video_study.knowledge.visual_retrieval import is_vlm_failure_source
from video_study.utils import TaskCancelled


class PipelineCharacterizationTests(unittest.TestCase):
    def test_public_pipeline_signatures_are_stable(self) -> None:
        expected = [
            "config", "video", "force", "force_summary", "cloud_summary",
            "force_asr", "qwen_settings", "asr_settings", "progress",
            "task_progress", "cancel_check", "event",
        ]
        self.assertEqual(list(inspect.signature(pipeline.process_video).parameters), expected)
        self.assertEqual(list(inspect.signature(pipeline.run_all).parameters), expected)

    def test_process_video_contract_uses_document_v3_and_local_source_url(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "lesson.mp4"
            video.write_bytes(b"offline-fixture")
            model_dir = root / "models"
            model_dir.mkdir()
            work_dir = root / "workspace" / "lesson-id"
            work_dir.mkdir(parents=True)
            output_dir = root / "output"
            config = AppConfig(root, {
                "paths": {
                    "workspace_dir": "workspace",
                    "output_dir": "output",
                    "model_dir": "models",
                },
                "asr": {"engine": "fake", "device": "cpu"},
                "frames": {"sample_interval_seconds": 10, "max_candidates": 10},
                "qwen": {"content_level": "推荐", "budget": {}},
                "render": {"source_link_base": "video-study://play"},
                "document": {"schema_version": 2},
            })
            manifest = {
                "video_id": "lesson-id",
                "title": "lesson",
                "source_path": str(video.resolve()),
                "duration_seconds": 12.0,
                "stages": {},
            }
            source_url = "video-study://play/lesson-id?t=1.25"
            document = {
                "schema_version": 2,
                "mode": "offline_summary",
                "model": None,
                "model_attempts": [],
                "cloud_usage": {},
                "sections": [{
                    "title": "章节",
                    "knowledge_points": [{
                        "title": "知识点",
                        "content_blocks": [{
                            "type": "paragraph",
                            "text": "来自离线 fixture 的正文",
                            "origin": "audio_backed",
                            "source_ids": ["seg_00001"],
                        }],
                        "source_refs": {
                            "segment_ids": ["seg_00001"],
                            "start_seconds": 1.25,
                            "end_seconds": 2.0,
                            "label": "00:01–00:02",
                            "url": source_url,
                            "links": [],
                        },
                    }],
                }],
                "knowledge_pipeline": {
                    "visual_runtime": {"question_count": 0, "gpu_used": False},
                },
            }
            events: list[dict] = []
            progress: list[tuple[str, str, int]] = []

            def fake_audio(_video: Path, output: Path, *_args, **_kwargs) -> Path:
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(b"audio")
                return output

            def fake_render(_document, output: Path, *_args, **_kwargs) -> None:
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(b"artifact")

            def fake_pdf(_document, _docx: Path, output: Path, *_args, **_kwargs) -> str:
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(b"%PDF-offline-fixture")
                return "built_in"

            def fake_frames(_video, output_dir: Path, _options, **_runtime):
                candidates = output_dir / "candidates"
                candidates.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (32, 24), "white").save(candidates / "candidate_00001.jpg")
                value = {
                    "schema_version": 1,
                    "sampling": {"sample_interval_seconds": 10.0, "max_width": 1280},
                    "candidates": [{
                        "candidate_id": "candidate_00001", "index": 0,
                        "timestamp_seconds": 0.0, "file": "candidate_00001.jpg",
                    }],
                }
                (output_dir / "candidates.json").write_text(
                    json.dumps(value), encoding="utf-8",
                )
                return value

            transcript_fixture = {
                "engine": "fake", "device": "cpu", "compute_type": "int8",
                "segments": [{
                    "segment_id": "seg_00001", "start_seconds": 1.25,
                    "end_seconds": 2.0, "text": "fixture",
                }],
                "runtime": {"cache_hit": False},
            }

            def fake_decode(_audio, output: Path, _options, **_runtime):
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(json.dumps(transcript_fixture), encoding="utf-8")
                return transcript_fixture

            with patch("video_study.media.MediaAdapter.probe", return_value={"format": {"duration": "12.0"}}), \
                    patch("video_study.media.MediaAdapter.extract_audio", side_effect=fake_audio), \
                    patch("video_study.asr.SpeechAdapter.decode", side_effect=fake_decode) as decode, \
                    patch("video_study.media.MediaAdapter.extract_frame_candidates", side_effect=fake_frames), \
                    patch("video_study.render.DocumentAdapter.render_markdown", side_effect=fake_render), \
                    patch("video_study.render.DocumentAdapter.render_word", side_effect=fake_render), \
                    patch("video_study.render.DocumentAdapter.render_pdf", side_effect=fake_pdf):
                result = pipeline.process_video(
                    config,
                    video,
                    progress=lambda *args: progress.append(args),
                    event=events.append,
                )

            self.assertEqual(set(result), {
                "video_id", "manifest", "markdown", "docx", "pdf", "pdf_mode",
                "mode", "model", "model_attempts", "cloud_usage", "runtime_events",
                "degradations", "asr_runtime", "visual_runtime", "compute_summary",
                "status", "editorial_mode", "degradation_summary",
            })
            self.assertTrue(result["video_id"].startswith("lesson-"))
            self.assertEqual(result["pdf_mode"], "built_in_v31")
            self.assertEqual(result["degradations"], [])
            self.assertEqual(
                [item["code"] for item in events if item.get("type") == "runtime"],
                [
                    "video_started", "audio_extract_started", "frame_candidates_started",
                    "frame_candidates_completed", "transcript_normalized", "frames_completed",
                    "render_started", "video_completed",
                ],
            )
            self.assertTrue(all(item["level"] == "info" for item in events))
            self.assertTrue(all(item.get("run_id") and item.get("step_id") and item.get("code") for item in events))
            self.assertEqual(progress[-1], ("completed", "处理完成", 100))
            saved = json.loads(Path(result["manifest"]).parent.joinpath("knowledge", "document-v3.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["schema_version"], 3)
            from video_study.document_v3 import v3_to_v2
            rendered_view = v3_to_v2(saved)
            self.assertEqual(
                rendered_view["sections"][0]["knowledge_points"][0]["source_refs"]["url"],
                f"video-study://play/{result['video_id']}?t=1",
            )

    def test_cancellation_stops_before_workspace_or_middleware_initialization(self) -> None:
        with TemporaryDirectory() as directory, patch("video_study.media.MediaAdapter.probe") as probe:
            root = Path(directory)
            video = root / "lesson.mp4"
            video.write_bytes(b"video")
            config = AppConfig(root, {"paths": {
                "workspace_dir": "workspace", "output_dir": "output", "model_dir": "models",
            }})
            with self.assertRaises(TaskCancelled):
                pipeline.process_video(config, video, cancel_check=lambda: True)
        probe.assert_not_called()

    def test_run_all_preserves_input_order_and_forwards_public_options(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            videos = [root / "b.mp4", root / "a.mp4"]
            config = AppConfig(root, {"paths": {"input_dir": "videos"}})
            with patch.object(pipeline, "discover_configured_videos", return_value=videos), \
                    patch.object(
                        pipeline,
                        "process_video",
                        side_effect=lambda _config, video, **_kwargs: {"video_id": video.stem},
                    ) as process:
                result = pipeline.run_all(
                    config,
                    force=True,
                    force_summary=True,
                    cloud_summary=False,
                    force_asr=True,
                )
            self.assertEqual(result, [{"video_id": "b"}, {"video_id": "a"}])
            self.assertEqual([call.args[1] for call in process.call_args_list], videos)
            self.assertTrue(all(call.kwargs["force"] for call in process.call_args_list))
            self.assertTrue(all(call.kwargs["force_summary"] for call in process.call_args_list))
            self.assertTrue(all(call.kwargs["force_asr"] for call in process.call_args_list))
            self.assertTrue(all(call.kwargs["cloud_summary"] is False for call in process.call_args_list))

    def test_visual_no_match_and_real_failure_remain_distinct(self) -> None:
        for expected_no_match in (
            "vlm_rejected", "vlm_criteria_rejected", "vlm_no_candidate",
            "fallback_no_candidate", "global_scene_arbitration",
        ):
            self.assertFalse(is_vlm_failure_source(expected_no_match), expected_no_match)
        for real_failure in (
            "vlm_provider_error", "vlm_oom_no_match", "vlm_detail_failed",
            "vlm_invalid_candidate_id",
        ):
            self.assertTrue(is_vlm_failure_source(real_failure), real_failure)


if __name__ == "__main__":
    unittest.main()
