from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from video_study.application.processing import DefaultProcessingService
from video_study.application.requests import CloudAuthorization, ProcessingHandle, ProcessingRequest, ProcessingResult
from video_study.config import AppConfig
from video_study.progress import ProgressEvent


class ProcessingServiceTests(unittest.TestCase):
    def test_public_dtos_are_secret_safe_and_handle_cancel_is_idempotent(self) -> None:
        auth = CloudAuthorization(
            True, "secret", "https://example.com/v1", ("model",),
            max_calls=2, editorial_brief="按主题重组",
        )
        request = ProcessingRequest(Path("lesson.mp4"), cloud=auth)
        self.assertNotIn("secret", repr(auth))
        self.assertNotIn("secret", repr(request))
        self.assertEqual(auth.legacy_settings({})["_runtime_editorial_brief"], "按主题重组")
        handle = ProcessingHandle()
        handle.cancel(); handle.cancel()
        self.assertTrue(handle.cancelled())

    def test_handle_replays_progress_published_before_ui_subscribes(self) -> None:
        handle = ProcessingHandle()
        handle.publish({"type": "progress", "stage": "audio", "progress": 10})
        received = []

        handle.subscribe(received.append)

        self.assertEqual(received, [{"type": "progress", "stage": "audio", "progress": 10}])

    def test_clear_and_delete_use_workspace_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); video = root / "lesson.mp4"; video.write_bytes(b"x")
            work = root / "workspace" / "lesson-id"; work.mkdir(parents=True)
            (work / "manifest.json").write_text(
                '{"video_id":"lesson-id","source_path":"' + str(video).replace('\\', '\\\\') + '"}', encoding="utf-8",
            )
            config = AppConfig(root, {"paths": {"workspace_dir": "workspace", "output_dir": "output"}})
            service = DefaultProcessingService(config)
            service.delete_video_workspace(video)
            self.assertFalse(work.exists())
            (root / "workspace" / "loose.tmp").write_text("x", encoding="utf-8")
            self.assertEqual(service.clear_workspace(), 1)

    def test_result_legacy_roundtrip(self) -> None:
        value = {"video_id": "id", "manifest": "m.json", "markdown": "a.md", "docx": "a.docx", "pdf": "a.pdf", "mode": "offline", "pdf_mode": "fallback"}
        result = ProcessingResult.from_legacy(value)
        self.assertEqual(result.to_legacy()["pdf_mode"], "fallback")

    def test_service_publishes_serialized_task_eta_for_desktop(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "lesson.mp4"
            video.write_bytes(b"video")
            config = AppConfig(root, {
                "paths": {"workspace_dir": "workspace", "output_dir": "output", "model_dir": "models"},
                "qwen": {}, "asr": {}, "frames": {}, "render": {},
            })

            def fake_process(_config, _video, **kwargs):
                kwargs["task_progress"](ProgressEvent(
                    "audio", "extract", 1, 2, False, 3,
                    task_id="audio.extract", cache_state="miss", bucket="ffmpeg",
                ))
                return {
                    "video_id": "id", "manifest": "m.json", "markdown": "a.md",
                    "docx": "a.docx", "pdf": "a.pdf",
                }

            with patch("video_study.application.processing.process_video", side_effect=fake_process):
                handle = DefaultProcessingService(config).process(ProcessingRequest(video))
                events = []
                handle.subscribe(events.append)
                handle.wait(1)

            self.assertEqual(events[0]["event"]["stage"], "audio")
            self.assertEqual(events[0]["event"]["eta_seconds"], 3)


if __name__ == "__main__":
    unittest.main()
