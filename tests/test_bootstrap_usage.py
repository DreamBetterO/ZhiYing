from __future__ import annotations

import unittest

from zhiying.execution.bootstrap import _run_cloud_usage
from zhiying.providers import CloudRequestBudget


class BootstrapUsageTests(unittest.TestCase):
    def test_shared_budget_usage_wins_over_editorial_stage_usage(self) -> None:
        budget = CloudRequestBudget(max_requests=10)
        budget.usage.update(prompt_tokens=600, completion_tokens=90, total_tokens=690)

        self.assertEqual(
            _run_cloud_usage(budget, {"usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}}),
            {"prompt_tokens": 600, "completion_tokens": 90, "total_tokens": 690},
        )

    def test_editorial_usage_is_offline_fallback(self) -> None:
        self.assertEqual(
            _run_cloud_usage(None, {"usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}),
            {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )


if __name__ == "__main__":
    unittest.main()
