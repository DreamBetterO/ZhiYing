import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from zhiying.runtime import find_tool


class RuntimeToolTests(unittest.TestCase):
    def test_project_local_ffmpeg_tools_are_found(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            ffprobe = root / "tools" / "ffmpeg" / "ffprobe.exe"
            ffprobe.parent.mkdir(parents=True)
            ffprobe.touch()

            with patch("zhiying.runtime.project_root", return_value=root):
                self.assertEqual(find_tool("ffprobe"), str(ffprobe))


if __name__ == "__main__":
    unittest.main()
