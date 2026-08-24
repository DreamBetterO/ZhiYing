from __future__ import annotations

import unittest

from zhiying.knowledge.cloud_info import merge_cloud_info
from zhiying.providers import CloudRequestBudget, ModelAttempt


class CloudInfoTests(unittest.TestCase):
    def test_cached_stage_usage_is_not_replaced_by_current_run_budget(self) -> None:
        planning = {
            "model": "planner",
            "attempts": [{"model": "planner", "ok": True, "error": None}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
        }
        organizing = {
            "model": "writer",
            "attempts": [{"model": "writer", "ok": True, "error": None}],
            "usage": {"prompt_tokens": 50, "completion_tokens": 30, "total_tokens": 80},
        }
        current_run = CloudRequestBudget(5)
        current_run.claim(stage="organizing", model="writer")
        current_run.record(
            stage="organizing",
            attempt=ModelAttempt("writer", True),
            usage=organizing["usage"],
        )

        merged = merge_cloud_info(planning, organizing, current_run)

        self.assertEqual(merged["usage"]["total_tokens"], 200)
        self.assertEqual(len(merged["attempts"]), 2)
        self.assertEqual(merged["request_budget"]["current_run_requests_used"], 1)
        self.assertEqual(merged["request_budget"]["lineage_attempts"], 2)


if __name__ == "__main__":
    unittest.main()
