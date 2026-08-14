"""全局单实例互斥测试：Windows mutex 命令矩阵与释放。"""
from __future__ import annotations

import sys
import unittest
from unittest.mock import patch, MagicMock

from video_study.single_instance import acquire_single_instance, _resource_key, SingleInstanceHandle


class SingleInstanceTests(unittest.TestCase):
    def test_resource_key_is_stable_and_unique_per_project(self) -> None:
        key_a = _resource_key("/project-a")
        key_b = _resource_key("/project-b")
        self.assertTrue(key_a.startswith("Global\\video-study-"))
        self.assertEqual(key_a, _resource_key("/project-a"))
        self.assertNotEqual(key_a, key_b)

    def test_acquire_returns_handle_on_first_call(self) -> None:
        if sys.platform != "win32":
            self.skipTest("仅 Windows")
        mock_handle = MagicMock()
        with patch("ctypes.windll.kernel32.CreateMutexW", return_value=mock_handle) as mock_create, \
             patch("ctypes.windll.kernel32.GetLastError", return_value=0):
            handle = acquire_single_instance("/test-project")
            self.assertIsInstance(handle, SingleInstanceHandle)
            mock_create.assert_called_once()

    def test_second_acquire_raises_when_mutex_exists(self) -> None:
        if sys.platform != "win32":
            self.skipTest("仅 Windows")
        mock_handle = MagicMock()
        ERROR_ALREADY_EXISTS = 183
        with patch("ctypes.windll.kernel32.CreateMutexW", return_value=mock_handle), \
             patch("ctypes.windll.kernel32.GetLastError", return_value=ERROR_ALREADY_EXISTS), \
             patch("ctypes.windll.kernel32.CloseHandle") as mock_close:
            with self.assertRaisesRegex(RuntimeError, "已有任务实例正在运行"):
                acquire_single_instance("/test-project-conflict")
            mock_close.assert_called_once_with(mock_handle)

    def test_release_closes_handle(self) -> None:
        if sys.platform != "win32":
            self.skipTest("仅 Windows")
        mock_handle = MagicMock()
        with patch("ctypes.windll.kernel32.ReleaseMutex") as mock_release, \
             patch("ctypes.windll.kernel32.CloseHandle") as mock_close:
            handle = SingleInstanceHandle("test", mock_handle)
            handle.release()
            mock_release.assert_called_once_with(mock_handle)
            mock_close.assert_called_once_with(mock_handle)

    def test_context_manager_releases_on_exit(self) -> None:
        if sys.platform != "win32":
            self.skipTest("仅 Windows")
        mock_handle = MagicMock()
        with patch("ctypes.windll.kernel32.CreateMutexW", return_value=mock_handle), \
             patch("ctypes.windll.kernel32.GetLastError", return_value=0), \
             patch("ctypes.windll.kernel32.ReleaseMutex"), \
             patch("ctypes.windll.kernel32.CloseHandle"):
            with acquire_single_instance("/test-ctx") as handle:
                self.assertTrue(handle._handle)
            self.assertFalse(handle._handle)


if __name__ == "__main__":
    unittest.main()
