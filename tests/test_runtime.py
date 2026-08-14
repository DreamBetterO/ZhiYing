import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from video_study.runtime import find_tool


class RuntimeToolTests(unittest.TestCase):
    def test_ffmpeg_sibling_tools_are_found_inside_portable_bundle(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            ffprobe = root / "tools" / "ffmpeg" / "ffprobe.exe"
            ffprobe.parent.mkdir(parents=True)
            ffprobe.touch()

            with (
                patch("video_study.runtime.resource_root", return_value=root),
                patch("video_study.runtime.executable_root", return_value=root),
            ):
                self.assertEqual(find_tool("ffprobe"), str(ffprobe))


if __name__ == "__main__":
    unittest.main()
