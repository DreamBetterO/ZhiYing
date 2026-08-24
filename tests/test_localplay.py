from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from zhiying.config import AppConfig
from zhiying.infrastructure.playback import launch_local_player, play_protocol_url, timestamp_url


class LocalPlaybackTests(unittest.TestCase):
    def test_timestamp_url_uses_local_protocol(self) -> None:
        self.assertEqual(timestamp_url("中文-id", 37), "video-study://play/%E4%B8%AD%E6%96%87-id?t=37")

    def test_protocol_resolves_manifest_and_seeks_locally(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); video = root / "课程.mp4"; video.touch()
            work = root / "workspace" / "中文-id"; work.mkdir(parents=True)
            (work / "manifest.json").write_text(json.dumps({"video_id": "中文-id", "source_path": str(video)}), encoding="utf-8")
            config = AppConfig(root, {"paths": {"workspace_dir": "workspace"}})
            with patch("zhiying.infrastructure.playback.launch_local_player", return_value=True) as launcher:
                self.assertTrue(play_protocol_url(config, timestamp_url("中文-id", 37)))
            launcher.assert_called_once_with(video, 37)

    def test_ffplay_receives_requested_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "lesson.mp4"; video.touch()
            with patch("zhiying.infrastructure.playback.shutil.which", return_value="ffplay.exe"), patch("zhiying.infrastructure.playback.subprocess.Popen") as popen:
                self.assertTrue(launch_local_player(video, 12.5))
            self.assertEqual(popen.call_args.args[0][:3], ["ffplay.exe", "-ss", "12.500"])


if __name__ == "__main__":
    unittest.main()
