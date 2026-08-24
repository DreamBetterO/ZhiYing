from __future__ import annotations

import unittest

from zhiying.execution.decision_policy import VisualNeedLevel
from zhiying.execution.graphs.visual_graph import VisualGraph


class V6VisualGraphTests(unittest.TestCase):
    def test_visual_graph_executes_evidence_once_and_projects_need(self) -> None:
        calls = []
        value = VisualGraph().run(
            VisualNeedLevel.SUPPORTIVE,
            execute=lambda: calls.append(True) or [{"decision": "no_match"}],
        )
        self.assertEqual(calls, [True])
        self.assertEqual(value["visual_need"], "supportive")
        self.assertEqual(value["evidence"][0]["decision"], "no_match")


if __name__ == "__main__":
    unittest.main()
