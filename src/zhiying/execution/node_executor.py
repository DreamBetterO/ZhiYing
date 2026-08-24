"""Single-node transaction executor; scheduling is owned by ``GraphRuntime``."""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from typing import Mapping

from ..progress import ProgressEvent
from .artifacts import ArtifactId, ArtifactRef, ArtifactStore
from .cache import WorkspaceCache
from .context import ProcessingContext
from .contracts import ErrorInfo, ExecutionCancelled, StepOutcome, StepStatus


@dataclass(frozen=True)
class NodeExecution:
    outcome: StepOutcome
    duration_seconds: float
    cache_reason: str | None


class NodeExecutor:
    def __init__(self, context: ProcessingContext, artifacts: ArtifactStore, cache: WorkspaceCache) -> None:
        self.context, self.artifacts, self.cache = context, artifacts, cache

    def run(self, step, inputs: Mapping[ArtifactId, ArtifactRef], transition) -> NodeExecution:
        step_id, staging, reason = step.spec.step_id, None, None
        started = time.monotonic()
        try:
            self._ensure_not_cancelled()
            fingerprint = step.fingerprint(self.context, inputs)
            transition(step_id, StepStatus.CHECKING_CACHE)
            self._lifecycle(step_id, StepStatus.CHECKING_CACHE, "step_cache_check_started", "正在检查步骤缓存")
            decision = self.cache.decide(self.context, step.spec, fingerprint, inputs)
            reason = decision.reason.value
            if decision.hit:
                if not all(self.artifacts.validate(self.context, ref) for ref in decision.artifacts):
                    raise RuntimeError("缓存返回了未通过 Artifact 校验的输出")
                outcome = StepOutcome(step_id, self.context.run_id, StepStatus.CACHED, decision.produced_capability, decision.artifacts, diagnostics={"cache_reason": reason})
                step.validate(self.context, outcome)
                self._progress(step_id, "hit")
                return self._result(outcome, started, reason)
            transition(step_id, StepStatus.READY)
            self._lifecycle(step_id, StepStatus.READY, "step_cache_miss", "步骤缓存未命中", cache_reason=reason)
            self._progress(step_id, "miss", completed=0.0)
            self._ensure_not_cancelled()
            staging = self.artifacts.staging_dir(self.context, step_id)
            transition(step_id, StepStatus.RUNNING)
            self._lifecycle(step_id, StepStatus.RUNNING, "step_execution_started", "开始执行步骤", cache_reason=reason)
            outcome = step.execute(self.context, inputs, staging)
            if outcome.step_id != step_id or outcome.run_id != self.context.run_id:
                raise ValueError("StepOutcome 身份与当前 Step/Run 不一致")
            if outcome.status not in {StepStatus.SUCCEEDED, StepStatus.DEGRADED, StepStatus.SKIPPED, StepStatus.FAILED, StepStatus.CANCELLED}:
                raise ValueError(f"execute 返回了非法终态：{outcome.status.value}")
            if outcome.status in {StepStatus.SUCCEEDED, StepStatus.DEGRADED}:
                step.validate(self.context, outcome)
                outcome = replace(outcome, artifacts=self.artifacts.commit(self.context, step.spec, outcome, staging))
                self.cache.record(self.context, step.spec, fingerprint, outcome)
                self._progress(step_id, "miss")
            return self._result(outcome, started, reason)
        except ExecutionCancelled as exc:
            return self._result(StepOutcome(step_id, self.context.run_id, StepStatus.CANCELLED, error=ErrorInfo("RUN_CANCELLED", str(exc), "ExecutionCancelled")), started, reason)
        except Exception as exc:
            return self._result(StepOutcome(step_id, self.context.run_id, StepStatus.FAILED, error=ErrorInfo(f"{step.spec.error_code_prefix}_UNHANDLED", str(exc), type(exc).__name__)), started, reason)
        finally:
            if staging is not None:
                self.artifacts.cleanup_staging(self.context, step_id)

    def _result(self, outcome, started, reason):
        return NodeExecution(outcome, time.monotonic() - started, reason)

    def _ensure_not_cancelled(self) -> None:
        if self.context.services.cancelled():
            raise ExecutionCancelled("任务已取消")

    def _progress(self, step_id, state, *, completed=1.0) -> None:
        self.context.services.progress_sink(ProgressEvent(stage=step_id.split(".", 1)[0], unit_kind="step", completed=completed, total=1.0, cache_hit=state == "hit", task_id=f"runner.{step_id}", cache_state=state, bucket=step_id))

    def _lifecycle(self, step_id, status, code, message, **diagnostics) -> None:
        try:
            self.context.services.event_sink({"type": "step_lifecycle", "run_id": self.context.run_id, "step_id": step_id, "stage": step_id.split(".", 1)[0], "level": "info", "message": message, "code": code, "status": status.value, **{key: value for key, value in diagnostics.items() if value is not None}})
        except Exception:
            pass
