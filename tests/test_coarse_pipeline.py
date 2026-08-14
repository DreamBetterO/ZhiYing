from __future__ import annotations

import json
import unittest
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image

from video_study.config import AppConfig
from video_study.execution.artifacts import ArtifactId
from video_study.execution.steps.coarse import build_coarse_steps
from video_study.pipeline import process_video
from video_study.utils import TaskCancelled


class CoarseProductionPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.video = self.root / "lesson.mp4"
        self.video.write_bytes(b"offline-video-fixture")
        (self.root / "models").mkdir()
        self.config = AppConfig(self.root, {
            "paths": {
                "workspace_dir": "workspace", "output_dir": "output", "model_dir": "models",
            },
            "asr": {"engine": "fake", "device": "cpu"},
            "frames": {"sample_interval_seconds": 10, "max_candidates": 10},
            "qwen": {"content_level": "推荐", "budget": {"max_calls_per_video": 5}},
            "render": {"source_link_base": "video-study://play", "include_full_transcript": True},
            "document": {"schema_version": 2},
            "visual_teaching": {"level": "auto"},
            "visual_evidence": {"enabled": False},
        })

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def _audio(_video, output: Path, **_runtime):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"audio")
        return output

    @staticmethod
    def _decode(_audio, output: Path, _options, **_runtime):
        transcript = {
            "engine": "fake", "device": "cpu", "compute_type": "int8",
            "segments": [{
                "segment_id": "seg_00001", "start_seconds": 0.0,
                "end_seconds": 1.0, "text": "离线测试内容",
            }],
            "runtime": {"cache_hit": False},
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(transcript, ensure_ascii=False), encoding="utf-8")
        return transcript

    @staticmethod
    def _frames(_video, output_dir: Path, _options, **_runtime):
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
        (output_dir / "candidates.json").write_text(json.dumps(value), encoding="utf-8")
        return value

    @staticmethod
    def _document(manifest, transcript, _frames, _output, *_args):
        video_id = manifest["video_id"]
        return {
            "schema_version": 2,
            "mode": "offline_summary",
            "model": None,
            "model_attempts": [],
            "cloud_usage": {},
            "metadata": {"video_id": video_id, "title": manifest["title"]},
            "sections": [{
                "title": "章节",
                "knowledge_points": [{
                    "title": "知识点",
                    "content_blocks": [{
                        "type": "paragraph", "text": "离线测试内容",
                        "origin": "audio_backed", "source_ids": ["seg_00001"],
                    }],
                    "source_refs": {
                        "segment_ids": ["seg_00001"], "start_seconds": 0,
                        "end_seconds": 1, "label": "00:00–00:01",
                        "url": f"video-study://play/{video_id}?t=0", "links": [],
                    },
                }],
            }],
            "transcript": transcript["segments"],
            "knowledge_pipeline": {"visual_runtime": {"question_count": 0, "gpu_used": False}},
        }

    @staticmethod
    def _render(_source, output: Path, **_runtime):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"rendered")
        return output

    @staticmethod
    def _pdf(_document, _word, output: Path, **_runtime):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"%PDF-fixture")
        return "built_in"

    def fake_middleware(self) -> ExitStack:
        stack = ExitStack()
        stack.enter_context(patch("video_study.media.MediaAdapter.probe", return_value={"format": {"duration": "1.0"}}))
        stack.enter_context(patch("video_study.media.MediaAdapter.extract_audio", side_effect=self._audio))
        stack.enter_context(patch("video_study.asr.SpeechAdapter.decode", side_effect=self._decode))
        stack.enter_context(patch("video_study.media.MediaAdapter.extract_frame_candidates", side_effect=self._frames))
        stack.enter_context(patch("video_study.render.DocumentAdapter.render_markdown", side_effect=self._render))
        stack.enter_context(patch("video_study.render.DocumentAdapter.render_word", side_effect=self._render))
        stack.enter_context(patch("video_study.render.DocumentAdapter.render_pdf", side_effect=self._pdf))
        return stack

    def test_registry_has_stable_steps_after_p6_knowledge_split(self) -> None:
        render = ArtifactId("render.bundle", ("lesson.md", "lesson.docx", "lesson.pdf"), "output")
        self.assertEqual(
            tuple(step.spec.step_id for step in build_coarse_steps(render)),
            (
                "source.probe", "audio.extract", "transcript.decode", "transcript.normalize",
                "frames.candidates", "frames.select", "knowledge.plan", "visual.jobs",
                "visual.evidence", "frames.semantics", "knowledge.course_ir",
                "knowledge.units", "knowledge.selfcheck", "document.assemble", "render.bundle",
            ),
        )

    def test_production_runner_outputs_compatibility_result_and_structured_state(self) -> None:
        events = []
        with self.fake_middleware():
            result = process_video(self.config, self.video, cloud_summary=False, event=events.append)
        self.assertEqual(set(result), {
            "video_id", "manifest", "markdown", "docx", "pdf", "pdf_mode", "mode",
            "model", "model_attempts", "cloud_usage", "runtime_events", "degradations",
            "asr_runtime", "visual_runtime", "compute_summary",
        })
        self.assertTrue(all(Path(result[key]).is_file() for key in ("manifest", "markdown", "docx", "pdf")))
        self.assertTrue(all(row.get("run_id") and row.get("step_id") and row.get("code") for row in events))
        state = json.loads((Path(result["manifest"]).parent / "state" / "pipeline-state.json").read_text(encoding="utf-8"))
        self.assertEqual(set(state["steps"]), {
            "source.probe", "audio.extract", "transcript.decode", "transcript.normalize",
            "frames.candidates", "frames.select", "knowledge.plan", "visual.jobs",
            "visual.evidence", "frames.semantics", "knowledge.course_ir",
            "knowledge.units", "knowledge.selfcheck", "document.assemble", "render.bundle",
        })
        self.assertTrue(all(value["status"] == "succeeded" for value in state["steps"].values()))
        run_log = Path(result["manifest"]).parent / "state" / "runs" / f"{state['run_id']}.jsonl"
        self.assertTrue(run_log.is_file())
        readable_log = run_log.with_suffix(".log")
        summary_log = run_log.with_suffix(".summary.json")
        self.assertTrue(readable_log.is_file())
        summary = json.loads(summary_log.read_text(encoding="utf-8"))
        self.assertEqual(summary["status"], "succeeded")
        self.assertEqual(len(summary["steps"]), 15)
        self.assertEqual(summary["metadata"]["work_type"], "video_processing")

    def test_second_fully_cached_run_constructs_no_middleware(self) -> None:
        with self.fake_middleware():
            first = process_video(self.config, self.video, cloud_summary=False)
        constructors = (
            "video_study.media.MediaAdapter",
            "video_study.asr.SpeechAdapter",
            "video_study.execution.adapters.vision.VisionAdapter",
            "video_study.render.DocumentAdapter",
            "video_study.utils.LocalProcessAdapter",
        )
        with ExitStack() as stack:
            mocks = [stack.enter_context(patch(name)) for name in constructors]
            second = process_video(self.config, self.video, cloud_summary=False)
        for constructor in mocks:
            constructor.assert_not_called()
        step_events = [row for row in second["runtime_events"] if row.get("type") == "step_state"]
        self.assertEqual(len(step_events), 15)
        self.assertTrue(all(row["status"] == "cached" for row in step_events))
        self.assertEqual(second["asr_runtime"]["cache_hit"], True)
        self.assertEqual(first["pdf"], second["pdf"])

    def test_cancelled_forced_step_preserves_previous_artifact_cache_and_releases_lease(self) -> None:
        with self.fake_middleware():
            result = process_video(self.config, self.video, cloud_summary=False)
        work = Path(result["manifest"]).parent
        audio = work / "audio" / "audio.flac"
        record = work / "state" / "cache" / "audio.extract.json"
        old_audio = audio.read_bytes()
        old_record = record.read_bytes()
        with patch("video_study.media.MediaAdapter.probe", side_effect=AssertionError("source should be cached")), \
                patch("video_study.media.MediaAdapter.extract_audio", side_effect=TaskCancelled("cancelled")):
            with self.assertRaises(TaskCancelled):
                process_video(self.config, self.video, force=True, cloud_summary=False)
        self.assertEqual(audio.read_bytes(), old_audio)
        self.assertEqual(record.read_bytes(), old_record)
        self.assertFalse((work / "state" / "workspace.lease.json").exists())
        self.assertEqual(list((work / "state" / "staging").rglob("audio.extract")), [])


if __name__ == "__main__":
    unittest.main()
