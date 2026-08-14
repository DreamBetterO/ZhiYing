from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from video_study.config import AppConfig, load_config


class ConfigIncludeTests(unittest.TestCase):
    def test_path_expands_windows_environment_variables(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ, {"VIDEO_STUDY_TEST_DATA": temp_dir}
        ):
            config = AppConfig(
                Path(temp_dir),
                {"paths": {"output_dir": "%VIDEO_STUDY_TEST_DATA%/output"}},
            )
            self.assertEqual(
                config.path("paths", "output_dir"),
                Path(temp_dir).resolve() / "output",
            )

    def test_api_yaml_is_merged_into_main_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "config.yaml").write_text(
                "api_config: api.yaml\npaths:\n  input_dir: Resource\n",
                encoding="utf-8",
            )
            (root / "api.yaml").write_text(
                "qwen:\n  enabled: false\n  default_models: [demo-model]\n",
                encoding="utf-8",
            )

            config = load_config(root / "config.yaml")

        self.assertEqual(config.raw["qwen"]["default_models"], ["demo-model"])
        self.assertEqual(config.raw["api_config"], "api.yaml")

    def test_duplicate_api_section_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "config.yaml").write_text(
                "api_config: api.yaml\nqwen: {}\n",
                encoding="utf-8",
            )
            (root / "api.yaml").write_text("qwen: {}\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "同时出现在"):
                load_config(root / "config.yaml")


if __name__ == "__main__":
    unittest.main()
