from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from video_study.config import AppConfig
from video_study.desktop.settings import (
    DesktopSettingsInput,
    save_source_download_dir,
    source_download_dir,
    validate_input,
)


class DesktopSettingsTests(unittest.TestCase):
    def test_validated_settings_do_not_contain_secret(self) -> None:
        raw = DesktopSettingsInput("https://example.com/v1", "model-a", "faster-whisper", api_key="secret")
        value = validate_input(raw)
        self.assertNotIn("secret", repr(raw))
        self.assertNotIn("secret", repr(value))

    def test_source_download_dir_defaults_to_project_video_folder(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = AppConfig(root, {"source": {}})
            self.assertEqual(source_download_dir(config), (root / "视频").resolve())

    def test_source_download_dir_reads_configured_value(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = AppConfig(root, {"source": {"download_dir": "我的视频"}})
            self.assertEqual(source_download_dir(config), (root / "我的视频").resolve())

    def test_source_download_dir_accepts_absolute_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            absolute = root / "outside"
            config = AppConfig(root, {"source": {"download_dir": str(absolute)}})
            self.assertEqual(source_download_dir(config), absolute.resolve())

    def test_save_source_download_dir_persists_and_reloads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config.yaml").write_text("source:\n  enabled: true\n", encoding="utf-8")
            config = AppConfig(root, {"source": {"enabled": True}})
            target = root / "下载视频"
            target.mkdir()

            resolved = save_source_download_dir(config, target)

            self.assertEqual(resolved, target.resolve())
            self.assertEqual(config.raw["source"]["download_dir"], str(target.resolve()))
            reloaded = AppConfig(root, {"source": {"download_dir": "下载视频"}})
            self.assertEqual(source_download_dir(reloaded), target.resolve())

    def test_save_source_download_dir_rejects_missing_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config.yaml").write_text("source: {}\n", encoding="utf-8")
            config = AppConfig(root, {"source": {}})
            with self.assertRaisesRegex(ValueError, "不是有效目录"):
                save_source_download_dir(config, root / "不存在")


if __name__ == "__main__":
    unittest.main()
