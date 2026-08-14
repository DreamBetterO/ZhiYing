from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Mapping

from video_study.execution.artifacts import ArtifactId, ArtifactRef, WorkspaceLayout
from video_study.execution.cache import CacheDecision, CacheReason
from video_study.execution.context import (
    ProcessingContext,
    ProcessingOptions,
    RunPolicy,
    RuntimeServices,
    VideoSource,
)
from video_study.execution.contracts import (
    ErrorInfo,
    ExecutionCancelled,
    FingerprintMaterial,
    StepOutcome,
    StepSpec,
    StepStatus,
)
from video_study.execution.registry import StepRegistry
from video_study.execution.runner import PipelineRunState, PipelineRunner


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
        self.assertEqual(runner.state.statuses[step.spec.step_id], StepStatus.CACHED)

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
