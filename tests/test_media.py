from __future__ import annotations

import unittest
from unittest.mock import patch

from video_study.media import check_asr_cuda_runtime, prepare_cuda_runtime


class CudaRuntimeDoctorTests(unittest.TestCase):
    def test_visible_gpu_is_not_reported_usable_when_a_required_dll_is_missing(self) -> None:
        with patch("video_study.media.os.name", "nt"), patch(
            "video_study.media.prepare_cuda_runtime", return_value=[]
        ), patch(
            "video_study.media.ctypes.WinDLL", side_effect=[object(), OSError("missing")]
        ):
            status = check_asr_cuda_runtime(True)

        self.assertFalse(status["available"])
        self.assertEqual(status["missing"], ["cudnn64_9.dll"])
        self.assertIn("CUDA", status["reason"])

    def test_prepare_cuda_runtime_registers_existing_environment_directories(self) -> None:
        with patch("video_study.media.os.name", "nt"), patch.dict(
            "video_study.media.os.environ", {"CONDA_PREFIX": ""}, clear=False
        ), patch(
            "video_study.media.os.add_dll_directory", return_value=object(), create=True
        ) as add_directory, patch("video_study.media.Path.is_dir", return_value=True), patch(
            "video_study.media.Path.is_file", return_value=True
        ), patch("video_study.media.ctypes.WinDLL", return_value=object()):
            added = prepare_cuda_runtime()

        self.assertEqual(len(added), 1)
        self.assertEqual(add_directory.call_count, 1)


if __name__ == "__main__":
    unittest.main()
