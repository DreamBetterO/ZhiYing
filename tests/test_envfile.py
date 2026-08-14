from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from video_study.envfile import import_qwen_txt


class EnvImportTests(unittest.TestCase):
    def test_import_keeps_models_and_budgets_out_of_env(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.txt"
            destination = root / ".env"
            source.write_text(
                "QWEN_API_KEY=test-only-key\n"
                "QWEN_BASE_URL=https://example.invalid/v1\n"
                "qwen3.7-plus\n",
                encoding="utf-8",
            )

            result = import_qwen_txt(source, destination)
            generated = destination.read_text(encoding="utf-8")

        self.assertFalse(result["cloud_enabled"])
        self.assertIn("QWEN_API_KEY=test-only-key", generated)
        self.assertNotIn("QWEN_MODEL_CHAIN", generated)
        self.assertNotIn("QWEN_MAX_INPUT_CHARS", generated)


if __name__ == "__main__":
    unittest.main()
