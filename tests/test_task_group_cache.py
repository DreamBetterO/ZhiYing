from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from video_study.execution.task_groups import FileTaskGroupCache


class FileTaskGroupCacheTests(unittest.TestCase):
    def test_each_dynamic_task_has_an_independent_fingerprint_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = FileTaskGroupCache(Path(directory), "visual.evidence")
            cache.record("job-a", {"frame": "a", "model": "m"}, {"decision": "select"})
            cache.record("job-b", {"frame": "b", "model": "m"}, {"decision": "no_match"})
            self.assertEqual(cache.load("job-a", {"frame": "a", "model": "m"})["decision"], "select")
            self.assertEqual(cache.load("job-b", {"frame": "b", "model": "m"})["decision"], "no_match")
            self.assertIsNone(cache.load("job-a", {"frame": "changed", "model": "m"}))
            self.assertEqual(len(list(Path(directory).rglob("*.json"))), 2)


if __name__ == "__main__":
    unittest.main()
