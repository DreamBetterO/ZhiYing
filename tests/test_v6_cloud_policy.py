from __future__ import annotations

import unittest

from zhiying.providers import (
    CloudCircuitOpen, CloudRequestBudget, ModelAttempt,
)


class V6CloudPolicyTests(unittest.TestCase):
    def test_repeated_failures_open_shared_circuit_before_next_request(self) -> None:
        budget = CloudRequestBudget(10, failure_limit=2)
        for index in range(2):
            budget.claim(stage=f"stage-{index}", model="fake")
            budget.record(
                stage=f"stage-{index}",
                attempt=ModelAttempt("fake", False, "fixture"),
                usage={},
            )
        self.assertTrue(budget.snapshot()["circuit_open"])
        with self.assertRaises(CloudCircuitOpen):
            budget.claim(stage="later", model="fake")
        self.assertEqual(budget.requests_used, 2)

    def test_success_resets_consecutive_failure_count(self) -> None:
        budget = CloudRequestBudget(10, failure_limit=2)
        budget.record(stage="a", attempt=ModelAttempt("fake", False, "x"), usage={})
        budget.record(stage="b", attempt=ModelAttempt("fake", True), usage={})
        self.assertEqual(budget.consecutive_failures, 0)
        self.assertFalse(budget.circuit_open)

    def test_open_circuit_reports_recent_safe_attempt_reasons(self) -> None:
        budget = CloudRequestBudget(10, failure_limit=2)
        budget.record(stage="blueprint", attempt=ModelAttempt("a", False, "timeout"), usage={})
        budget.record(stage="blueprint", attempt=ModelAttempt("b", False, "HTTP 503"), usage={})
        with self.assertRaisesRegex(CloudCircuitOpen, "a: timeout.*b: HTTP 503"):
            budget.claim(stage="blueprint", model="c")


if __name__ == "__main__":
    unittest.main()
