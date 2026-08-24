import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from zhiying.progress import EtaEstimator, ProgressEvent


class EtaEstimatorTests(unittest.TestCase):
    def test_without_history_unknown_work_has_no_estimate(self) -> None:
        estimator = EtaEstimator()
        estimator.observe(ProgressEvent("visual", "compare", 0, 4, False))
        self.assertIsNone(estimator.estimate())

    def test_warm_jobs_use_recent_median_and_recalculate_changed_total(self) -> None:
        estimator = EtaEstimator()
        estimator.observe(ProgressEvent("visual", "compare", 1, 4, False, 10))
        estimator.observe(ProgressEvent("visual", "compare", 2, 4, False, 8))
        self.assertEqual(estimator.estimate(), 18)
        estimator.observe(ProgressEvent("visual", "compare", 2, 7, False))
        self.assertEqual(estimator.estimate(), 45)

    def test_cache_hit_never_reuses_non_cached_samples(self) -> None:
        estimator = EtaEstimator()
        estimator.observe(ProgressEvent("frames", "candidate", 1, 3, False, 4))
        estimator.observe(ProgressEvent("frames", "candidate", 0, 2, True))
        self.assertIsNone(estimator.estimate())
        estimator.observe(ProgressEvent("frames", "candidate", 1, 2, True, 0.2))
        self.assertAlmostEqual(estimator.estimate(), 0.2)

    def test_history_is_sanitized_and_bounded(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "eta-history.json"
            estimator = EtaEstimator(
                path, hardware="RTX", model="Qwen", content_level="推荐",
            )
            for index in range(24):
                estimator.observe(ProgressEvent("visual", "compare", index + 1, 30, False, index + 1))
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["version"], 2)
            self.assertEqual(len(payload["samples"]), 20)
            self.assertNotIn("video", json.dumps(payload).lower())
            self.assertEqual(payload["samples"][-1]["hardware"], "RTX")

    def test_task_graph_keeps_independent_dynamic_jobs(self) -> None:
        estimator = EtaEstimator()
        estimator.register([
            ProgressEvent(
                "visual", "compare", 0, 1,
                task_id="visual.compare.job1", cache_state="unknown", bucket="vlm-a",
            ),
            ProgressEvent(
                "visual", "compare", 0, 1,
                task_id="visual.compare.job2", cache_state="unknown", bucket="vlm-a",
            ),
        ])
        estimator.observe(ProgressEvent(
            "visual", "compare", 1, 1, False, 10,
            task_id="visual.compare.job1", cache_state="miss", bucket="vlm-a",
        ))
        self.assertIsNone(estimator.estimate())
        estimator.observe(ProgressEvent(
            "visual", "compare", 0, 1, False,
            task_id="visual.compare.job2", cache_state="miss", bucket="vlm-a",
        ))
        self.assertEqual(estimator.estimate(), 10)

    def test_history_is_bounded_per_bucket_not_globally(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "eta-history.json"
            estimator = EtaEstimator(path)
            for index in range(25):
                estimator.observe(ProgressEvent(
                    "visual", "compare", 1, 1, False, index + 1,
                    task_id=f"a{index}", cache_state="miss", bucket="model-a",
                ))
                estimator.observe(ProgressEvent(
                    "visual", "compare", 1, 1, False, index + 1,
                    task_id=f"b{index}", cache_state="miss", bucket="model-b",
                ))
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(len(payload["samples"]), 40)
            self.assertEqual({row["bucket"] for row in payload["samples"]}, {"model-a", "model-b"})

    def test_clear_removes_live_estimate_but_keeps_history(self) -> None:
        estimator = EtaEstimator()
        estimator.observe(ProgressEvent("render", "word", 0, 1, False, 3))
        self.assertEqual(estimator.estimate(), 3)
        estimator.clear()
        self.assertEqual(estimator.estimate(), 0)


if __name__ == "__main__":
    unittest.main()
