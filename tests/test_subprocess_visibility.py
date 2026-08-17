from __future__ import annotations

import subprocess
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from video_study import utils
from video_study.asr import _decode_qwen_audio
from video_study.execution.adapters.vision import QwenVLSession


class SubprocessVisibilityTests(unittest.TestCase):
    @unittest.skipUnless(hasattr(subprocess, "CREATE_NO_WINDOW"), "Windows-only behavior")
    def test_background_process_kwargs_hide_windows_console(self) -> None:
        kwargs = utils.background_process_kwargs()
        self.assertTrue(kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW)

    @unittest.skipUnless(hasattr(subprocess, "CREATE_NO_WINDOW"), "Windows-only behavior")
    def test_shared_run_uses_background_process_policy(self) -> None:
        completed = subprocess.CompletedProcess(["tool.exe"], 0, "ok", "")
        with patch("video_study.utils.subprocess.run", return_value=completed) as runner:
            self.assertIs(utils.run(["tool.exe"], capture=True), completed)
        self.assertTrue(runner.call_args.kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW)

    @unittest.skipUnless(hasattr(subprocess, "CREATE_NO_WINDOW"), "Windows-only behavior")
    def test_cancellable_process_uses_background_process_policy(self) -> None:
        process = MagicMock()
        process.poll.return_value = 0
        process.returncode = 0
        with patch("video_study.utils.subprocess.Popen", return_value=process) as popen:
            utils.run_cancellable(["tool.exe"])
        self.assertTrue(popen.call_args.kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW)

    @unittest.skipUnless(hasattr(subprocess, "CREATE_NO_WINDOW"), "Windows-only behavior")
    def test_qwen_asr_runtime_is_started_without_console(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio = root / "audio.wav"
            python = root / "runtime" / "python.exe"
            model = root / "model"
            runner = root / "runner.py"
            audio.write_bytes(b"audio")
            python.parent.mkdir()
            python.touch()
            model.mkdir()
            runner.touch()
            process = MagicMock()
            process.poll.return_value = 1
            process.returncode = 1
            process.stdout = io.StringIO("")
            process.stderr = io.StringIO("")
            settings = {
                "_config_root": str(root),
                "qwen_runtime_python": str(python),
                "qwen_runtime_dir": str(python.parent),
                "qwen_model_dir": str(model),
                "qwen_runner": str(runner),
            }
            with patch("video_study.asr.subprocess.Popen", return_value=process) as popen:
                with self.assertRaises(RuntimeError):
                    _decode_qwen_audio(audio, root / "temporary", settings, None)
        self.assertTrue(popen.call_args.kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW)

    @unittest.skipUnless(hasattr(subprocess, "CREATE_NO_WINDOW"), "Windows-only behavior")
    def test_qwen_vl_runtime_is_started_without_console(self) -> None:
        session = QwenVLSession(
            python=Path("python.exe"), runner=Path("runner.py"), runtime=Path("runtime"),
            model=Path("model"), timeout=30.0, settings={},
        )
        process = MagicMock()
        process.poll.return_value = None
        process.stdout = None
        process.stderr = None
        with (
            patch("video_study.execution.adapters.vision.subprocess.Popen", return_value=process) as popen,
            patch("video_study.execution.adapters.vision.threading.Thread"),
            patch.object(session, "_wait_for", return_value={"model_load_count": 1}),
        ):
            session.start()
        self.assertTrue(popen.call_args.kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW)
        temporary = session._temporary
        session.process = None
        session._temporary = None
        if temporary is not None:
            temporary.cleanup()


if __name__ == "__main__":
    unittest.main()
