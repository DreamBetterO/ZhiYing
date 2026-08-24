from __future__ import annotations

import tempfile
import unittest
from unittest.mock import MagicMock, patch

from pathlib import Path

from zhiying.execution.adapters.vision import (
    LocalQwenVLProvider,
    QwenVLSession,
    VisionAdapter,
    VisualProviderSessionError,
    create_ocr_provider,
    create_visual_provider,
)


class VisionProviderTests(unittest.TestCase):
    def test_vision_adapter_projects_nested_and_step_settings_to_provider(self) -> None:
        adapter = VisionAdapter(
            {"visual_evidence": {"local_vlm_enabled": "auto", "setting_a": 1}},
            cancel_check=lambda: False,
            event_sink=lambda _event: None,
            progress_sink=lambda _event: None,
        )
        provider = MagicMock()
        with patch(
            "zhiying.execution.adapters.vision.create_visual_provider",
            return_value=(provider, ""),
        ) as create:
            session = adapter.open_session({"setting_b": 2})
        settings = create.call_args.args[0]
        self.assertEqual(settings["local_vlm_enabled"], "auto")
        self.assertEqual(settings["setting_a"], 1)
        self.assertEqual(settings["setting_b"], 2)
        self.assertIs(session.provider, provider)

    def test_qwen_session_start_creates_temporary_workspace(self) -> None:
        session = QwenVLSession(
            python=Path("python.exe"),
            runner=Path("runner.py"),
            runtime=Path("runtime"),
            model=Path("model"),
            timeout=30.0,
            settings={},
        )
        process = MagicMock()
        process.poll.return_value = None
        process.stdout = None
        process.stderr = None
        with (
            patch("zhiying.execution.adapters.vision.subprocess.Popen", return_value=process),
            patch("zhiying.execution.adapters.vision.threading.Thread"),
            patch.object(session, "_wait_for", return_value={"model_load_count": 1}),
        ):
            session.start()
        self.assertIsNotNone(session.root)
        self.assertEqual(session.model_load_count, 1)
        temporary = session._temporary
        session.process = None
        session._temporary = None
        if temporary is not None:
            temporary.cleanup()

    def test_ocr_is_opt_in(self) -> None:
        provider, warning = create_ocr_provider({"ocr_enabled": False})
        self.assertIsNone(provider)
        self.assertIn("未启用", warning)

    def test_local_vlm_is_opt_in(self) -> None:
        provider, warning = create_visual_provider({"local_vlm_enabled": False})
        self.assertIsNone(provider)
        self.assertIn("未启用", warning)

    def test_missing_local_weights_degrade_without_subprocess(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            provider, warning = create_visual_provider({
                "local_vlm_enabled": True,
                "_config_root": temp,
                "local_vlm_runtime_python": "missing/python.exe",
                "local_vlm_runtime_dir": "missing/runtime",
                "local_vlm_model_dir": "missing/model",
                "local_vlm_runner": "missing/runner.py",
            })
        self.assertIsNone(provider)
        self.assertIn("缺少", warning)

    def test_local_vlm_auto_uses_complete_local_gpu_runtime(self) -> None:
        with patch.object(LocalQwenVLProvider, "preflight", return_value={
            "ok": True, "cuda_available": True, "device": "Test GPU",
        }):
            provider, warning = create_visual_provider({"local_vlm_enabled": "auto"})
        self.assertIsInstance(provider, LocalQwenVLProvider)
        self.assertEqual(warning, "")

    def test_session_restart_retries_only_unfinished_job(self) -> None:
        session = QwenVLSession(
            python=Path("python.exe"),
            runner=Path("runner.py"),
            runtime=Path("runtime"),
            model=Path("model"),
            timeout=30.0,
            settings={},
        )
        calls: list[str] = []

        def submit_once(job_id, payload):
            calls.append(job_id)
            if job_id == "job2" and calls.count("job2") == 1:
                raise VisualProviderSessionError("runner crashed")
            return {"decision": "no_match", "job": job_id}

        with patch.object(session, "_submit_once", side_effect=submit_once):
            completed = session.run_jobs([("job1", {}), ("job2", {})])
        self.assertEqual(calls, ["job1", "job2", "job2"])
        self.assertEqual(set(completed), {"job1", "job2"})


if __name__ == "__main__":
    unittest.main()
