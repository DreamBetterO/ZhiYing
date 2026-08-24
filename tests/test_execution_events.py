from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from zhiying.execution.artifacts import WorkspaceLayout
from zhiying.execution.events import RunEventJournal


class RunEventJournalTests(unittest.TestCase):
    def test_each_run_writes_structured_readable_and_summary_logs_without_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            layout = WorkspaceLayout(Path(temp) / "workspace", "video-id")
            journal = RunEventJournal(layout, "run-id")
            journal.start({
                "work_type": "video_processing",
                "source": {"path": Path(temp) / "lesson.mp4"},
                "api_key": "must-not-leak",
            })
            journal.publish({
                "type": "step_state", "step_id": "fixture.step", "status": "succeeded",
                "code": "step_succeeded", "message": "步骤完成", "capability": "offline",
                "diagnostics": {"count": 1},
            })
            journal.finish("succeeded", outputs={"pdf": Path(temp) / "lesson.pdf"})

            summary = json.loads(journal.summary_path.read_text(encoding="utf-8"))
            summary_text = journal.summary_path.read_text(encoding="utf-8")
            events = [json.loads(line) for line in journal.jsonl_path.read_text(encoding="utf-8").splitlines()]
            readable = journal.text_path.read_text(encoding="utf-8")

        self.assertEqual(summary["status"], "succeeded")
        self.assertEqual(summary["steps"]["fixture.step"]["status"], "succeeded")
        self.assertEqual(summary["metadata"]["api_key"], "<redacted>")
        self.assertEqual(events[0]["code"], "run_started")
        self.assertEqual(events[-1]["code"], "run_succeeded")
        self.assertIn("fixture.step step_succeeded 步骤完成", readable)
        self.assertNotIn("must-not-leak", summary_text)

    def test_failed_run_summary_keeps_exception_and_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            journal = RunEventJournal(WorkspaceLayout(Path(temp), "video-id"), "run-failed")
            journal.start({"work_type": "video_processing"})
            error = RuntimeError("真实失败")
            journal.finish("failed", error=error, traceback_text="Traceback: fixture")
            summary = json.loads(journal.summary_path.read_text(encoding="utf-8"))

        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary["error"]["exception_type"], "RuntimeError")
        self.assertEqual(summary["error"]["message"], "真实失败")
        self.assertIn("Traceback", summary["error"]["traceback"])


if __name__ == "__main__":
    unittest.main()
