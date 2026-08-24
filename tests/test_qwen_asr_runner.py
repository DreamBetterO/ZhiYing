from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.workers.qwen_asr_runner as runner


class QwenAsrRunnerTests(unittest.TestCase):
    def test_qwen3_asr_model_requires_official_backend(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            model = Path(temp)
            (model / "config.json").write_text(json.dumps({
                "model_type": "qwen3_asr",
                "architectures": ["Qwen3ASRForConditionalGeneration"],
            }), encoding="utf-8")

            self.assertTrue(runner._model_requires_official_backend(str(model)))

    def test_missing_official_dependency_is_reported_before_legacy_fallback(self) -> None:
        modules = {"qwen_asr": "qwen-asr", "nagisa": "nagisa"}

        def fake_find_spec(name: str):
            return object() if name == "qwen_asr" else None

        with patch("scripts.workers.qwen_asr_runner.importlib.util.find_spec", side_effect=fake_find_spec):
            self.assertEqual(runner._missing_modules(modules), ["nagisa"])

    def test_speed_threshold_is_warning_only_after_samples(self) -> None:
        self.assertFalse(runner._speed_warning_required([0.9], 0.75, False))
        self.assertTrue(runner._speed_warning_required([0.8, 0.85], 0.75, False))
        self.assertFalse(runner._speed_warning_required([0.8, 0.85], 0.75, True))


if __name__ == "__main__":
    unittest.main()
