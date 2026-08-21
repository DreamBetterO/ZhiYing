from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from video_study.application.requests import JobRequest, ProcessingRequest
from video_study.execution.graphs.aggregate_graph import AggregateGraph
from video_study.execution.graphs.job_graph import JobGraph
from video_study.execution.graphs.source_graph import SourceGraph


class V6JobGraphTests(unittest.TestCase):
    def test_job_request_validates_aggregate_mode_and_cloud_authorization(self) -> None:
        source = ProcessingRequest(video=Path("lesson.mp4"))
        self.assertEqual(JobRequest((source,)).aggregate_mode, "none")
        with self.assertRaisesRegex(ValueError, "aggregate_mode"):
            JobRequest((source,), aggregate_mode="invalid")
        with self.assertRaisesRegex(ValueError, "云端授权"):
            JobRequest((source,), aggregate_mode="cloud")

    def test_source_graph_resolves_and_verifies_local_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "lesson.mp4"
            video.write_bytes(b"video")
            value = SourceGraph().run_local(video)
        self.assertEqual(value["status"], "succeeded")
        self.assertEqual(Path(value["verified_source"]["path"]), video.resolve())

    def test_job_graph_is_sequential_and_preserves_completed_results_on_failure(self) -> None:
        calls: list[str] = []

        def process(source: str) -> dict:
            calls.append(source)
            if source == "b":
                raise RuntimeError("fixture failure")
            return {"video_id": source}

        with self.assertRaisesRegex(RuntimeError, "fixture failure") as captured:
            JobGraph().run(("a", "b", "c"), process=process)
        self.assertEqual(calls, ["a", "b"])
        self.assertEqual(getattr(captured.exception, "completed_results"), ({"video_id": "a"},))

    def test_job_graph_routes_optional_aggregate_once(self) -> None:
        aggregate_calls: list[tuple[str, ...]] = []
        value = JobGraph().run(
            ("a", "b"),
            process=lambda source: {"video_id": source},
            aggregate_mode="local",
            aggregate=lambda results: aggregate_calls.append(tuple(row["video_id"] for row in results)) or {"video_id": "aggregate"},
        )
        self.assertEqual(aggregate_calls, [("a", "b")])
        self.assertEqual(value["aggregate_result"]["video_id"], "aggregate")
        self.assertNotIn("runtime_events", value.get("aggregate_result_ref", {}))

    def test_aggregate_graph_rejects_cloud_without_runtime_authorization(self) -> None:
        graph = AggregateGraph()
        with self.assertRaisesRegex(ValueError, "云端授权"):
            graph.run("cloud", ({"video_id": "a"}, {"video_id": "b"}), execute=lambda _rows: {})

    def test_job_checkpoint_persists_refs_not_runtime_events_or_secrets(self) -> None:
        from video_study.execution.checkpointing import SqliteCheckpointAdapter

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "job.sqlite3"
            adapter = SqliteCheckpointAdapter(database, "v6-job-1")
            try:
                value = JobGraph().run(
                    ("a",),
                    process=lambda source: {
                        "video_id": source, "manifest": "m.json",
                        "runtime_events": [{"api_key": "top-secret"}],
                    },
                    checkpoint_adapter=adapter,
                    thread_id="job-1",
                )
                checkpoint = adapter.saver.get_tuple(adapter.config_for("job-1"))
            finally:
                adapter.close()
            stored = database.read_bytes()
        self.assertIsNotNone(checkpoint)
        self.assertNotIn(b"top-secret", stored)
        self.assertEqual(value["video_result_refs"][0]["video_id"], "a")


if __name__ == "__main__":
    unittest.main()
