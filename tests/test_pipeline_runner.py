from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Mapping
from types import SimpleNamespace

from video_study.execution.artifacts import ArtifactId, ArtifactRef, WorkspaceLayout
from video_study.execution.cache import CacheDecision, CacheReason
from video_study.execution.context import (
    ProcessingContext,
    ProcessingOptions,
    RunPolicy,
    RuntimeServices,
    VideoSource,
)
from video_study.execution.node_executor import NodeExecutor
from video_study.execution.contracts import (
    ErrorInfo,
    ExecutionCancelled,
    FingerprintMaterial,
    StepOutcome,
    StepSpec,
    StepStatus,
)
from video_study.execution.registry import StepRegistry
from video_study.execution.graph_runtime import GraphRuntime
from video_study.execution.run_state import GraphRunState


class GraphTestRunner:
    """Test convenience adapter; production has no legacy scheduler."""

    def __init__(self, context, registry, artifacts, cache) -> None:
        self.context = context
        self.registry = registry
        self.artifacts = artifacts
        self.cache = cache
        self.node_executor = NodeExecutor(context, artifacts, cache)
        self.state = GraphRunState(context.run_id)

    def run(self, targets=None):
        kernel = SimpleNamespace(
            context=self.context, registry=self.registry,
            artifacts=self.artifacts, cache=self.cache,
        )
        self.state = GraphRuntime().run_compatible_state(kernel)
        return self.state


PipelineRunner = GraphTestRunner
PipelineRunState = GraphRunState


@dataclass
class FakeStep:
    spec: StepSpec
    terminal_status: StepStatus = StepStatus.SUCCEEDED
    execute_calls: int = 0
    validate_calls: int = 0
    raise_cancelled: bool = False

    def fingerprint(self, _context, inputs: Mapping) -> FingerprintMaterial:
        return FingerprintMaterial({"input_count": len(inputs)})

    def execute(self, context, _inputs: Mapping, staging_dir: Path) -> StepOutcome:
        self.execute_calls += 1
        if self.raise_cancelled:
            raise ExecutionCancelled("fake cancellation")
        staging_dir.mkdir(parents=True, exist_ok=True)
        artifacts = ()
        if self.terminal_status in {StepStatus.SUCCEEDED, StepStatus.DEGRADED}:
            output = staging_dir / "payload.json"
            output.write_text("{}", encoding="utf-8")
            artifacts = (ArtifactRef(self.spec.outputs[0], output, digest="sha256:fixture"),)
        error = None
        if self.terminal_status == StepStatus.FAILED:
            error = ErrorInfo(code="FAKE_FAILED", message="fake failure")
        if self.terminal_status == StepStatus.CANCELLED:
            error = ErrorInfo(code="RUN_CANCELLED", message="fake cancellation")
        return StepOutcome(
            self.spec.step_id,
            context.run_id,
            self.terminal_status,
            capability="offline",
            artifacts=artifacts,
            error=error,
        )

    def validate(self, _context, _outcome: StepOutcome) -> None:
        self.validate_calls += 1


@dataclass
class FakeArtifactStore:
    root: Path
    valid: bool = True
    staging_calls: int = 0
    commit_calls: int = 0
    cleanup_calls: int = 0

    def staging_dir(self, context, step_id: str) -> Path:
        self.staging_calls += 1
        return context.workspace.staging_dir(context.run_id, step_id)

    def validate(self, _context, _artifact: ArtifactRef) -> bool:
        return self.valid

    def commit(self, context, spec, outcome, _staging_dir: Path) -> tuple[ArtifactRef, ...]:
        self.commit_calls += 1
        committed = []
        for artifact in outcome.artifacts:
            target = context.workspace.artifact_paths(artifact.artifact_id)[0]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("{}", encoding="utf-8")
            committed.append(ArtifactRef(artifact.artifact_id, target, artifact.digest))
        return tuple(committed)

    def cleanup_staging(self, _context, _step_id: str) -> None:
        self.cleanup_calls += 1


@dataclass
class FakeCache:
    decision: CacheDecision = field(default_factory=lambda: CacheDecision(False, CacheReason.NO_RECORD))
    decide_calls: int = 0
    records: list[StepOutcome] = field(default_factory=list)

    def decide(self, _context, _spec, _fingerprint, _inputs) -> CacheDecision:
        self.decide_calls += 1
        return self.decision

    def record(self, _context, _spec, _fingerprint, outcome: StepOutcome) -> None:
        self.records.append(outcome)


def make_step(
    step_id: str = "fixture.step",
    status: StepStatus = StepStatus.SUCCEEDED,
    *,
    dependencies: tuple[str, ...] = (),
) -> FakeStep:
    artifact = ArtifactId(f"{step_id}.artifact", (f"artifacts/{step_id}.json",))
    return FakeStep(StepSpec(
        step_id=step_id,
        implementation_version=1,
        dependencies=dependencies,
        outputs=(artifact,),
        owner="tests",
        tests=("tests/test_pipeline_runner.py",),
        error_code_prefix="FIXTURE",
    ), terminal_status=status)


class PipelineRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        root = Path(self.temp.name)
        self.events: list[dict] = []
        self.progress_events = []
        self.context = ProcessingContext(
            "run-id",
            VideoSource(root / "lesson.mp4", "lesson-id", "sha256:fixture", 1.0, 1),
            WorkspaceLayout(root / "workspace", "lesson-id"),
            ProcessingOptions(),
            RunPolicy(),
            RuntimeServices(
                event_sink=self.events.append,
                progress_sink=self.progress_events.append,
            ),
        )
        self.store = FakeArtifactStore(root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_step(self, step: FakeStep, cache: FakeCache | None = None):
        cache = cache or FakeCache()
        runner = PipelineRunner(self.context, StepRegistry([step]), self.store, cache)
        return runner.run(), cache

    def test_cache_hit_does_not_execute_or_create_staging(self) -> None:
        step = make_step()
        ref = ArtifactRef(step.spec.outputs[0], Path("cached.json"), digest="sha256:cached")
        state, cache = self.run_step(step, FakeCache(CacheDecision(
            True, CacheReason.CACHE_HIT, (ref,), "offline",
        )))
        self.assertEqual(state.statuses[step.spec.step_id], StepStatus.CACHED)
        self.assertEqual(step.execute_calls, 0)
        self.assertEqual(self.store.staging_calls, 0)
        self.assertEqual(cache.records, [])
        self.assertEqual(self.progress_events[-1].cache_state, "hit")

    def test_runner_uses_node_executor_for_each_unblocked_step(self) -> None:
        step = make_step()
        runner = PipelineRunner(self.context, StepRegistry([step]), self.store, FakeCache())
        self.assertIsInstance(runner.node_executor, NodeExecutor)
        state = runner.run()
        self.assertEqual(state.statuses[step.spec.step_id], StepStatus.SUCCEEDED)

    def test_production_contains_no_legacy_runner_module_or_call(self) -> None:
        import video_study.execution as execution
        from video_study.execution import bootstrap

        self.assertFalse(hasattr(execution, "PipelineRunner"))
        self.assertNotIn(".runner().run", Path(bootstrap.__file__).read_text(encoding="utf-8"))

    def test_graph_runtime_matches_single_step_runner_terminal_status(self) -> None:
        from video_study.execution.graph_runtime import GraphRuntime

        step = make_step()
        kernel = SimpleNamespace(
            context=self.context,
            registry=StepRegistry([step]),
            artifacts=self.store,
            cache=FakeCache(),
        )
        result = GraphRuntime().run_single_video(kernel)
        self.assertEqual(result["statuses"][step.spec.step_id], StepStatus.SUCCEEDED.value)
        self.assertEqual(result["outcomes"][step.spec.step_id]["status"], StepStatus.SUCCEEDED.value)

    def test_graph_runtime_writes_same_version_sqlite_checkpoint(self) -> None:
        from video_study.execution.checkpointing import SqliteCheckpointAdapter
        from video_study.execution.graph_runtime import GraphRuntime

        step = make_step()
        kernel = SimpleNamespace(context=self.context, registry=StepRegistry([step]), artifacts=self.store, cache=FakeCache())
        database = Path(self.temp.name) / "graph.sqlite3"
        adapter = SqliteCheckpointAdapter(database, "v6-alpha-1")
        try:
            result = GraphRuntime().run_single_video(kernel, checkpoint_adapter=adapter, thread_id="job-1")
            self.assertEqual(result["statuses"][step.spec.step_id], "succeeded")
            self.assertIsNotNone(adapter.saver.get_tuple(adapter.config_for("job-1")))
        finally:
            adapter.close()

    def test_graph_runtime_same_version_resume_does_not_rerun_completed_node(self) -> None:
        from video_study.execution.checkpointing import SqliteCheckpointAdapter
        from video_study.execution.graph_runtime import GraphRuntime

        step = make_step()
        kernel = SimpleNamespace(context=self.context, registry=StepRegistry([step]), artifacts=self.store, cache=FakeCache())
        adapter = SqliteCheckpointAdapter(Path(self.temp.name) / "resume.sqlite3", "v6-alpha-1")
        try:
            runtime = GraphRuntime()
            runtime.run_single_video(kernel, checkpoint_adapter=adapter, thread_id="job-1")
            runtime.run_single_video(kernel, checkpoint_adapter=adapter, thread_id="job-1", resume=True)
            self.assertEqual(step.execute_calls, 1)
        finally:
            adapter.close()

    def test_graph_runtime_resume_preserves_completed_dependency_chain(self) -> None:
        from video_study.execution.checkpointing import SqliteCheckpointAdapter
        from video_study.execution.graph_runtime import GraphRuntime

        first, second = make_step("first"), make_step("second", dependencies=("first",))
        kernel = SimpleNamespace(context=self.context, registry=StepRegistry([first, second]), artifacts=self.store, cache=FakeCache())
        adapter = SqliteCheckpointAdapter(Path(self.temp.name) / "chain.sqlite3", "v6-alpha-1")
        try:
            runtime = GraphRuntime()
            runtime.run_single_video(kernel, checkpoint_adapter=adapter, thread_id="job-1")
            runtime.run_single_video(kernel, checkpoint_adapter=adapter, thread_id="job-1", resume=True)
            self.assertEqual((first.execute_calls, second.execute_calls), (1, 1))
        finally:
            adapter.close()

    def test_graph_runtime_resumes_from_interrupted_middle_node(self) -> None:
        from video_study.execution.checkpointing import SqliteCheckpointAdapter
        from video_study.execution.graph_runtime import GraphRuntime

        first, second = make_step("first"), make_step("second", dependencies=("first",))
        kernel = SimpleNamespace(context=self.context, registry=StepRegistry([first, second]), artifacts=self.store, cache=FakeCache())
        adapter = SqliteCheckpointAdapter(Path(self.temp.name) / "interrupt.sqlite3", "v6-alpha-1")
        try:
            runtime = GraphRuntime()
            runtime.run_single_video(kernel, checkpoint_adapter=adapter, thread_id="job-1", interrupt_after=("first",))
            self.assertEqual((first.execute_calls, second.execute_calls), (1, 0))
            runtime.run_single_video(kernel, checkpoint_adapter=adapter, thread_id="job-1", resume=True)
            self.assertEqual((first.execute_calls, second.execute_calls), (1, 1))
        finally:
            adapter.close()

    def test_graph_runtime_executes_fifteen_node_chain_with_projected_state(self) -> None:
        from video_study.execution.graph_runtime import GraphRuntime

        ids = [
            "source.probe", "audio.extract", "transcript.decode", "transcript.normalize",
            "frames.candidates", "frames.select", "knowledge.plan", "visual.jobs",
            "visual.evidence", "frames.semantics", "knowledge.course_ir", "knowledge.units",
            "knowledge.selfcheck", "document.assemble", "render.bundle",
        ]
        steps = [make_step(step_id, dependencies=(ids[index - 1],) if index else ()) for index, step_id in enumerate(ids)]
        kernel = SimpleNamespace(context=self.context, registry=StepRegistry(steps), artifacts=self.store, cache=FakeCache())
        result = GraphRuntime().run_single_video(kernel)
        self.assertEqual(list(result["statuses"]), ids)
        self.assertTrue(all(status == StepStatus.SUCCEEDED.value for status in result["statuses"].values()))
        self.assertTrue(all(result["outcomes"][step_id]["artifacts"] for step_id in ids))

    def test_fifteen_node_runner_and_graph_have_equivalent_terminal_statuses(self) -> None:
        from video_study.execution.graph_runtime import GraphRuntime

        ids = [f"fixture.step-{index:02d}" for index in range(15)]
        runner_steps = [make_step(step_id, dependencies=(ids[index - 1],) if index else ()) for index, step_id in enumerate(ids)]
        graph_steps = [make_step(step_id, dependencies=(ids[index - 1],) if index else ()) for index, step_id in enumerate(ids)]
        runner_state = PipelineRunner(self.context, StepRegistry(runner_steps), self.store, FakeCache()).run()
        graph_kernel = SimpleNamespace(context=self.context, registry=StepRegistry(graph_steps), artifacts=FakeArtifactStore(Path(self.temp.name) / "graph"), cache=FakeCache())
        graph_state = GraphRuntime().run_single_video(graph_kernel)
        self.assertEqual(
            {step_id: status.value for step_id, status in runner_state.statuses.items()},
            graph_state["statuses"],
        )

    def test_fifteen_node_runner_and_graph_have_equivalent_terminal_events(self) -> None:
        from video_study.execution.graph_runtime import GraphRuntime

        ids = [f"fixture.event-{index:02d}" for index in range(15)]
        runner_events, graph_events = [], []
        runner_context = ProcessingContext(self.context.run_id, self.context.source, self.context.workspace, self.context.options, self.context.policy, RuntimeServices(event_sink=runner_events.append))
        graph_context = ProcessingContext(self.context.run_id, self.context.source, self.context.workspace, self.context.options, self.context.policy, RuntimeServices(event_sink=graph_events.append))
        runner_steps = [make_step(step_id, dependencies=(ids[index - 1],) if index else ()) for index, step_id in enumerate(ids)]
        graph_steps = [make_step(step_id, dependencies=(ids[index - 1],) if index else ()) for index, step_id in enumerate(ids)]
        PipelineRunner(runner_context, StepRegistry(runner_steps), FakeArtifactStore(Path(self.temp.name) / "runner"), FakeCache()).run()
        GraphRuntime().run_single_video(SimpleNamespace(context=graph_context, registry=StepRegistry(graph_steps), artifacts=FakeArtifactStore(Path(self.temp.name) / "graph"), cache=FakeCache()))
        stable = lambda rows: [(row["step_id"], row["status"], row["code"]) for row in rows if row.get("type") == "step_state"]
        self.assertEqual(stable(runner_events), stable(graph_events))

    def test_fifteen_node_runner_and_graph_have_equivalent_cache_reasons(self) -> None:
        from video_study.execution.graph_runtime import GraphRuntime

        ids = [f"fixture.cache-{index:02d}" for index in range(15)]
        steps = [make_step(step_id, dependencies=(ids[index - 1],) if index else ()) for index, step_id in enumerate(ids)]
        refs = {step.spec.step_id: ArtifactRef(step.spec.outputs[0], Path(f"{step.spec.step_id}.json"), digest="sha256:cached") for step in steps}
        class HitCache(FakeCache):
            def decide(self, _context, spec, _fingerprint, _inputs):
                return CacheDecision(True, CacheReason.CACHE_HIT, (refs[spec.step_id],), "offline")
        events = []
        context = ProcessingContext(self.context.run_id, self.context.source, self.context.workspace, self.context.options, self.context.policy, RuntimeServices(event_sink=events.append))
        graph_steps = [make_step(step_id, dependencies=(ids[index - 1],) if index else ()) for index, step_id in enumerate(ids)]
        result = GraphRuntime().run_single_video(SimpleNamespace(context=context, registry=StepRegistry(graph_steps), artifacts=FakeArtifactStore(Path(self.temp.name)), cache=HitCache()))
        self.assertTrue(all(status == StepStatus.CACHED.value for status in result["statuses"].values()))
        terminal = [row for row in events if row.get("type") == "step_state"]
        self.assertTrue(all(row.get("cache_reason") == CacheReason.CACHE_HIT.value for row in terminal))

    def test_graph_runtime_preserves_failure_and_downstream_skip(self) -> None:
        from video_study.execution.graph_runtime import GraphRuntime

        first = make_step("first")
        failed = make_step("failed", StepStatus.FAILED, dependencies=("first",))
        blocked = make_step("blocked", dependencies=("failed",))
        result = GraphRuntime().run_single_video(SimpleNamespace(context=self.context, registry=StepRegistry([first, failed, blocked]), artifacts=self.store, cache=FakeCache()))
        self.assertEqual(result["statuses"], {"first": "succeeded", "failed": "failed", "blocked": "skipped"})
        self.assertEqual(result["outcomes"]["blocked"]["diagnostics"]["blocked_by"], ["failed"])

    def test_terminal_event_failure_preserves_original_error_and_terminal_state(self) -> None:
        step = make_step()
        ref = ArtifactRef(step.spec.outputs[0], Path("cached.json"), digest="sha256:cached")
        context = ProcessingContext(
            self.context.run_id, self.context.source, self.context.workspace,
            self.context.options, self.context.policy,
            RuntimeServices(event_sink=lambda _event: (_ for _ in ()).throw(RuntimeError("journal failed"))),
        )
        runner = PipelineRunner(
            context, StepRegistry([step]), self.store,
            FakeCache(CacheDecision(True, CacheReason.CACHE_HIT, (ref,), "offline")),
        )
        with self.assertRaisesRegex(RuntimeError, "journal failed"):
            runner.run()
        self.assertEqual(step.execute_calls, 0)

    def test_run_state_rejects_illegal_transition(self) -> None:
        state = PipelineRunState("run-id")
        state.transition("step", StepStatus.PENDING)
        with self.assertRaisesRegex(ValueError, "非法 Step 状态跃迁"):
            state.transition("step", StepStatus.SUCCEEDED)

    def test_succeeded_and_degraded_outputs_are_committed_then_cached(self) -> None:
        for terminal in (StepStatus.SUCCEEDED, StepStatus.DEGRADED):
            with self.subTest(terminal=terminal):
                step = make_step(status=terminal)
                self.store = FakeArtifactStore(Path(self.temp.name))
                state, cache = self.run_step(step)
                self.assertEqual(state.statuses[step.spec.step_id], terminal)
                self.assertEqual(self.store.commit_calls, 1)
                self.assertEqual(len(cache.records), 1)
                self.assertTrue(cache.records[0].artifacts[0].path.is_file())
                self.assertEqual(self.progress_events[-1].cache_state, "miss")

    def test_commit_failure_is_terminal_failed_and_never_records_cache(self) -> None:
        class FailingStore(FakeArtifactStore):
            def commit(self, *_args):
                self.commit_calls += 1
                raise RuntimeError("commit failed")

        self.store = FailingStore(Path(self.temp.name))
        state, cache = self.run_step(make_step())
        outcome = state.outcomes["fixture.step"]
        self.assertEqual(outcome.status, StepStatus.FAILED)
        self.assertEqual(outcome.error.code, "FIXTURE_UNHANDLED")
        self.assertEqual(cache.records, [])
        self.assertEqual(self.store.cleanup_calls, 1)

    def test_cache_record_failure_preserves_committed_artifact_and_reports_failure(self) -> None:
        class FailingCache(FakeCache):
            def record(self, *_args) -> None:
                raise RuntimeError("cache record failed")

        state, _cache = self.run_step(make_step(), FailingCache())
        outcome = state.outcomes["fixture.step"]
        self.assertEqual(outcome.status, StepStatus.FAILED)
        self.assertTrue(self.context.workspace.artifact_paths(make_step().spec.outputs[0])[0].is_file())
        self.assertEqual(self.store.cleanup_calls, 1)

    def test_invalid_cache_artifact_becomes_failure_without_step_execution(self) -> None:
        step = make_step()
        self.store.valid = False
        ref = ArtifactRef(step.spec.outputs[0], Path("cached.json"), digest="sha256:cached")
        state, _cache = self.run_step(step, FakeCache(CacheDecision(True, CacheReason.CACHE_HIT, (ref,), "offline")))
        self.assertEqual(state.statuses[step.spec.step_id], StepStatus.FAILED)
        self.assertEqual(step.execute_calls, 0)

    def test_skipped_and_failed_do_not_commit_cache_records(self) -> None:
        for terminal in (StepStatus.SKIPPED, StepStatus.FAILED):
            with self.subTest(terminal=terminal):
                step = make_step(status=terminal)
                self.store = FakeArtifactStore(Path(self.temp.name))
                state, cache = self.run_step(step)
                self.assertEqual(state.statuses[step.spec.step_id], terminal)
                self.assertEqual(self.store.commit_calls, 0)
                self.assertEqual(cache.records, [])

    def test_step_cancellation_produces_cancelled_without_cache_record(self) -> None:
        step = make_step()
        step.raise_cancelled = True
        state, cache = self.run_step(step)
        self.assertEqual(state.statuses[step.spec.step_id], StepStatus.CANCELLED)
        self.assertEqual(state.outcomes[step.spec.step_id].error.code, "RUN_CANCELLED")
        self.assertEqual(cache.records, [])

    def test_pre_cancelled_run_never_checks_cache_or_initializes_artifacts(self) -> None:
        context = ProcessingContext(
            self.context.run_id,
            self.context.source,
            self.context.workspace,
            self.context.options,
            self.context.policy,
            RuntimeServices(cancel_check=lambda: True),
        )
        step = make_step()
        cache = FakeCache()
        state = PipelineRunner(context, StepRegistry([step]), self.store, cache).run()
        self.assertEqual(state.statuses[step.spec.step_id], StepStatus.CANCELLED)
        self.assertEqual(cache.decide_calls, 0)
        self.assertEqual(self.store.staging_calls, 0)

    def test_failed_dependency_only_skips_necessary_downstream(self) -> None:
        failed = make_step("source", StepStatus.FAILED)
        blocked = make_step("dependent", dependencies=("source",))
        independent = make_step("independent")
        cache = FakeCache()
        runner = PipelineRunner(
            self.context,
            StepRegistry([failed, blocked, independent]),
            self.store,
            cache,
        )
        state = runner.run()
        self.assertEqual(state.statuses["source"], StepStatus.FAILED)
        self.assertEqual(state.statuses["dependent"], StepStatus.SKIPPED)
        self.assertEqual(state.outcomes["dependent"].diagnostics["blocked_by"], ["source"])
        self.assertEqual(state.statuses["independent"], StepStatus.SUCCEEDED)

    def test_cancelled_step_stops_independent_remaining_work(self) -> None:
        cancelled = make_step("cancelled")
        cancelled.raise_cancelled = True
        independent = make_step("independent")
        state = PipelineRunner(
            self.context,
            StepRegistry([cancelled, independent]),
            self.store,
            FakeCache(),
        ).run()
        self.assertEqual(state.statuses["cancelled"], StepStatus.CANCELLED)
        self.assertEqual(state.statuses["independent"], StepStatus.CANCELLED)
        self.assertEqual(independent.execute_calls, 0)


if __name__ == "__main__":
    unittest.main()
