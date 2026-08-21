"""V6 P0: read-only characterization fixtures and target-contract guards."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import TypedDict

import yaml

from video_study.execution.artifacts import DOCUMENT_V2, STANDARD_ARTIFACTS
from video_study.execution.artifacts import ArtifactId
from video_study.execution.contracts import ErrorInfo, StepOutcome, StepStatus
from video_study.execution.steps.coarse import build_coarse_steps


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "v6"


class V6P0ContractTests(unittest.TestCase):
    def load_fixture(self, name: str) -> dict:
        return json.loads((FIXTURES / name).read_text(encoding="utf-8"))

    def test_single_video_fixture_characterizes_current_23_step_contract(self) -> None:
        fixture = self.load_fixture("single_video_success.json")
        specs = [step.spec for step in build_coarse_steps(DOCUMENT_V2)]
        self.assertEqual(fixture["terminal_status"], "succeeded")
        self.assertEqual(fixture["step_ids"], [spec.step_id for spec in specs])
        self.assertEqual(fixture["artifact_ids"], list(STANDARD_ARTIFACTS))
        self.assertEqual(fixture["cloud_requests"], 0)

    def test_failure_cache_cancel_source_and_aggregate_fixtures_are_offline_and_stable(self) -> None:
        cache = self.load_fixture("cache_hit.json")
        cancellation = self.load_fixture("cancellation.json")
        failure = self.load_fixture("node_failure.json")
        sources = self.load_fixture("source_entrypoints.json")
        aggregates = self.load_fixture("aggregate_modes.json")
        self.assertEqual((cache["cache_reason"], cache["execute_calls"]), ("CACHE_HIT", 0))
        self.assertEqual((cancellation["terminal_status"], cancellation["cache_recorded"]), ("cancelled", False))
        self.assertIn(failure["step_id"], [step.spec.step_id for step in build_coarse_steps(DOCUMENT_V2)])
        self.assertEqual(sources["url"]["network_requests"], 0)
        self.assertEqual(aggregates["cloud"]["provider"], "fake")
        for fixture in (cache, cancellation, failure, sources, aggregates):
            self.assertEqual(fixture.get("cloud_requests", 0), 0)

    def test_v6_target_contract_and_active_release_candidate_are_consistent(self) -> None:
        target = yaml.safe_load((ROOT / "docs/迭代升级/V6.0 LangGraph全链路目标合同.yaml").read_text(encoding="utf-8"))
        current = yaml.safe_load((ROOT / "docs/迭代升级/当前架构升级状态.yaml").read_text(encoding="utf-8"))
        self.assertEqual(target["status"], "implemented_release_candidate")
        self.assertEqual(target["baseline"]["orchestrator"], "PipelineRunner")
        self.assertEqual(current["baseline"]["active_architecture"], "V6 LangGraph 23-node full-pipeline runtime with DocumentPlan/Document v3")
        self.assertIn("CP0", target["phases"])

    def test_graph_runtime_is_the_production_orchestration_boundary_after_cp3(self) -> None:
        from video_study.execution.graph_runtime import GraphRuntime

        self.assertTrue(GraphRuntime.production_enabled())

    def test_p3_graphs_cover_source_job_and_aggregate_boundaries(self) -> None:
        from video_study.execution.graphs.aggregate_graph import AggregateGraph
        from video_study.execution.graphs.job_graph import JobGraph
        from video_study.execution.graphs.source_graph import SourceGraph

        self.assertEqual(SourceGraph.node_ids(), (
            "source.local.resolve", "source.url.preflight", "source.url.acquire", "source.verify",
        ))
        self.assertEqual(JobGraph.node_ids(), (
            "job.validate", "source.resolve", "video.queue.next", "video.run",
            "aggregate.route", "aggregate.run", "job.finalize",
        ))
        self.assertEqual(AggregateGraph.node_ids(), (
            "aggregate.validate", "aggregate.route", "aggregate.run", "aggregate.finalize",
        ))

    def test_graph_runtime_topology_matches_the_current_twenty_three_steps(self) -> None:
        from video_study.execution.graph_runtime import GraphRuntime

        render = ArtifactId("render.bundle", ("lesson.md", "lesson.docx", "lesson.pdf"), "output")
        specs = [step.spec for step in build_coarse_steps(render)]
        runtime = GraphRuntime()
        self.assertEqual(runtime.topology(specs), tuple(spec.step_id for spec in specs))
        self.assertEqual(len(runtime.topology(specs)), 23)

    def test_graph_state_projection_is_bounded_and_excludes_secrets(self) -> None:
        from video_study.execution.graph_state import outcome_projection

        outcome = StepOutcome("fixture.step", "run", StepStatus.FAILED, error=ErrorInfo("FIXTURE", "x" * 800), diagnostics={"api_key": "forbidden", "safe": "ok"})
        value = outcome_projection(outcome)
        self.assertEqual(len(value["error"]["message"]), 500)
        self.assertEqual(value["diagnostics"], {"safe": "ok"})

    def test_checkpoint_adapter_rejects_cross_graph_version_resume(self) -> None:
        from video_study.execution.checkpointing import SqliteCheckpointAdapter

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "checkpoints.sqlite3"
            first = SqliteCheckpointAdapter(database, "v6-alpha-1")
            self.assertEqual(first.config_for("job-1")["configurable"]["thread_id"], "job-1")
            first.close()
            second = SqliteCheckpointAdapter(database, "v6-alpha-2")
            with self.assertRaisesRegex(ValueError, "GRAPH_VERSION_MISMATCH"):
                second.config_for("job-1")
            second.close()

    def test_sqlite_checkpointer_storage_probe_is_local_durable_and_reference_only(self) -> None:
        from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
        from langgraph.checkpoint.sqlite import SqliteSaver
        from langgraph.graph import END, START, StateGraph

        class ProbeState(TypedDict):
            artifact_refs: list[str]

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "checkpoint.sqlite3"
            connection = sqlite3.connect(database, check_same_thread=False)
            try:
                builder = StateGraph(ProbeState)
                builder.add_node("pass_through", lambda state: state)
                builder.add_edge(START, "pass_through")
                builder.add_edge("pass_through", END)
                saver = SqliteSaver(
                    connection,
                    serde=JsonPlusSerializer(allowed_msgpack_modules=[]),
                )
                graph = builder.compile(checkpointer=saver)
                result = graph.invoke(
                    {"artifact_refs": ["document.v2"]},
                    {"configurable": {"thread_id": "job-1"}},
                )
                checkpoint = saver.get_tuple({"configurable": {"thread_id": "job-1"}})
            finally:
                connection.close()
        self.assertEqual(result["artifact_refs"], ["document.v2"])
        self.assertIsNotNone(checkpoint)
        self.assertNotIn("api_key", repr(checkpoint))


if __name__ == "__main__":
    unittest.main()
