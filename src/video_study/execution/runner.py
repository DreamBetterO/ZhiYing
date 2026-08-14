from __future__ import annotations

from dataclasses import dataclass, field, replace
import time
from types import MappingProxyType
from typing import Mapping

from ..progress import ProgressEvent
from .artifacts import ArtifactId, ArtifactRef, ArtifactStore
from .cache import WorkspaceCache
from .context import ProcessingContext
from .contracts import (
    ErrorInfo,
    ExecutionCancelled,
    StepOutcome,
    StepStatus,
)
from .registry import StepRegistry


@dataclass
class PipelineRunState:
    run_id: str
    statuses: dict[str, StepStatus] = field(default_factory=dict)
    outcomes: dict[str, StepOutcome] = field(default_factory=dict)

    def transition(self, step_id: str, status: StepStatus) -> None:
        current = self.statuses.get(step_id)
        allowed = {
            None: {StepStatus.PENDING},
            StepStatus.PENDING: {StepStatus.CHECKING_CACHE, StepStatus.SKIPPED, StepStatus.CANCELLED},
            StepStatus.CHECKING_CACHE: {StepStatus.CACHED, StepStatus.READY, StepStatus.FAILED, StepStatus.CANCELLED},
            StepStatus.READY: {StepStatus.RUNNING, StepStatus.FAILED, StepStatus.CANCELLED},
            StepStatus.RUNNING: {
                StepStatus.SUCCEEDED, StepStatus.DEGRADED, StepStatus.SKIPPED,
                StepStatus.FAILED, StepStatus.CANCELLED,
            },
        }
        if status not in allowed.get(current, set()):
            previous = current.value if current else "<new>"
            raise ValueError(f"非法 Step 状态跃迁：{step_id} {previous} -> {status.value}")
        self.statuses[step_id] = status

    def snapshot(self) -> Mapping[str, str]:
        return MappingProxyType({key: value.value for key, value in self.statuses.items()})


class PipelineRunner:
    def __init__(
        self,
        context: ProcessingContext,
        registry: StepRegistry,
        artifacts: ArtifactStore,
        cache: WorkspaceCache,
    ) -> None:
        self.context = context
        self.registry = registry
        self.artifacts = artifacts
        self.cache = cache
        self.state = PipelineRunState(context.run_id)

    def run(self, targets: tuple[str, ...] | None = None) -> PipelineRunState:
        requested = targets or self.context.policy.target_steps or None
        order = self.registry.required_order(requested)
        for step_id in order:
            self.state.transition(step_id, StepStatus.PENDING)
        for step_id in order:
            if self._cancelled() or StepStatus.CANCELLED in self.state.statuses.values():
                self._set_cancelled(step_id)
                continue
            step = self.registry.get(step_id)
            blocked = [
                dependency
                for dependency in step.spec.dependencies
                if self.state.statuses.get(dependency) in {
                    StepStatus.FAILED, StepStatus.CANCELLED, StepStatus.SKIPPED,
                }
            ]
            if blocked:
                outcome = StepOutcome(
                    step_id=step_id,
                    run_id=self.context.run_id,
                    status=StepStatus.SKIPPED,
                    diagnostics={"blocked_by": blocked},
                )
                self._finish(outcome)
                continue
            self._run_step(step_id)
        return self.state

    def _run_step(self, step_id: str) -> None:
        step = self.registry.get(step_id)
        inputs = self._inputs_for(step.spec.dependencies)
        staging = None
        started_at = time.monotonic()
        cache_reason: str | None = None
        try:
            self._ensure_not_cancelled()
            fingerprint = step.fingerprint(self.context, inputs)
            self.state.transition(step_id, StepStatus.CHECKING_CACHE)
            self._publish_lifecycle(
                step_id, StepStatus.CHECKING_CACHE, "step_cache_check_started", "正在检查步骤缓存",
            )
            decision = self.cache.decide(self.context, step.spec, fingerprint, inputs)
            cache_reason = decision.reason.value
            if decision.hit:
                self._publish_lifecycle(
                    step_id, StepStatus.CACHED, "step_cache_hit", "步骤缓存命中",
                    cache_reason=cache_reason,
                )
                if not all(self.artifacts.validate(self.context, ref) for ref in decision.artifacts):
                    raise RuntimeError("缓存返回了未通过 Artifact 校验的输出")
                outcome = StepOutcome(
                    step_id=step_id,
                    run_id=self.context.run_id,
                    status=StepStatus.CACHED,
                    capability=decision.produced_capability,
                    artifacts=decision.artifacts,
                    diagnostics={"cache_reason": decision.reason.value},
                )
                step.validate(self.context, outcome)
                self._publish_cache_progress(step_id, "hit")
                self._finish(
                    outcome,
                    duration_seconds=time.monotonic() - started_at,
                    extra_diagnostics={"cache_reason": cache_reason},
                )
                return
            self.state.transition(step_id, StepStatus.READY)
            self._publish_lifecycle(
                step_id, StepStatus.READY, "step_cache_miss", "步骤缓存未命中",
                cache_reason=cache_reason,
            )
            self._publish_cache_progress(step_id, "miss", completed=0.0)
            self._ensure_not_cancelled()
            staging = self.artifacts.staging_dir(self.context, step_id)
            self.state.transition(step_id, StepStatus.RUNNING)
            self._publish_lifecycle(
                step_id, StepStatus.RUNNING, "step_execution_started", "开始执行步骤",
                cache_reason=cache_reason,
            )
            outcome = step.execute(self.context, inputs, staging)
            if outcome.step_id != step_id or outcome.run_id != self.context.run_id:
                raise ValueError("StepOutcome 身份与当前 Step/Run 不一致")
            if outcome.status in {StepStatus.PENDING, StepStatus.CHECKING_CACHE, StepStatus.READY, StepStatus.RUNNING, StepStatus.CACHED}:
                raise ValueError(f"execute 返回了非法终态：{outcome.status.value}")
            if outcome.status == StepStatus.CANCELLED:
                self._finish(
                    outcome, duration_seconds=time.monotonic() - started_at,
                    extra_diagnostics={"cache_reason": cache_reason},
                )
                return
            if outcome.status == StepStatus.FAILED:
                self._finish(
                    outcome, duration_seconds=time.monotonic() - started_at,
                    extra_diagnostics={"cache_reason": cache_reason},
                )
                return
            step.validate(self.context, outcome)
            if outcome.status in {StepStatus.SUCCEEDED, StepStatus.DEGRADED}:
                committed = self.artifacts.commit(self.context, step.spec, outcome, staging)
                outcome = replace(outcome, artifacts=committed)
                self.cache.record(self.context, step.spec, fingerprint, outcome)
                self._publish_cache_progress(step_id, "miss")
            self._finish(
                outcome, duration_seconds=time.monotonic() - started_at,
                extra_diagnostics={"cache_reason": cache_reason},
            )
        except ExecutionCancelled as exc:
            self._set_cancelled(step_id, str(exc), duration_seconds=time.monotonic() - started_at)
        except Exception as exc:
            if self.state.statuses.get(step_id) in {
                StepStatus.CACHED, StepStatus.SUCCEEDED, StepStatus.DEGRADED,
                StepStatus.SKIPPED, StepStatus.FAILED, StepStatus.CANCELLED,
            }:
                raise
            self._finish(StepOutcome(
                step_id=step_id,
                run_id=self.context.run_id,
                status=StepStatus.FAILED,
                error=ErrorInfo(
                    code=f"{step.spec.error_code_prefix}_UNHANDLED",
                    message=str(exc),
                    exception_type=type(exc).__name__,
                ),
            ),
                duration_seconds=time.monotonic() - started_at,
                extra_diagnostics={"cache_reason": cache_reason},
            )
        finally:
            if staging is not None:
                self.artifacts.cleanup_staging(self.context, step_id)

    def _inputs_for(self, dependencies: tuple[str, ...]) -> Mapping[ArtifactId, ArtifactRef]:
        refs: dict[ArtifactId, ArtifactRef] = {}
        for dependency in dependencies:
            outcome = self.state.outcomes.get(dependency)
            if outcome:
                refs.update({ref.artifact_id: ref for ref in outcome.artifacts})
        return MappingProxyType(refs)

    def _cancelled(self) -> bool:
        return self.context.services.cancelled()

    def _ensure_not_cancelled(self) -> None:
        if self._cancelled():
            raise ExecutionCancelled("任务已取消")

    def _set_cancelled(
        self, step_id: str, message: str = "任务已取消", *, duration_seconds: float | None = None,
    ) -> None:
        self._finish(StepOutcome(
            step_id=step_id,
            run_id=self.context.run_id,
            status=StepStatus.CANCELLED,
            error=ErrorInfo(code="RUN_CANCELLED", message=message, exception_type="ExecutionCancelled"),
        ), duration_seconds=duration_seconds)

    def _publish_cache_progress(
        self, step_id: str, cache_state: str, *, completed: float = 1.0,
    ) -> None:
        self.context.services.progress_sink(ProgressEvent(
            stage=step_id.split(".", 1)[0],
            unit_kind="step",
            completed=completed,
            total=1.0,
            cache_hit=cache_state == "hit",
            task_id=f"runner.{step_id}",
            cache_state=cache_state,
            bucket=step_id,
        ))

    def _publish_lifecycle(
        self, step_id: str, status: StepStatus, code: str, message: str, **diagnostics,
    ) -> None:
        try:
            self.context.services.event_sink({
                "type": "step_lifecycle",
                "run_id": self.context.run_id,
                "step_id": step_id,
                "stage": step_id.split(".", 1)[0],
                "level": "info",
                "message": message,
                "code": code,
                "status": status.value,
                **{key: value for key, value in diagnostics.items() if value is not None},
            })
        except Exception:
            # 诊断增强事件是 best-effort；终态事件仍保持原有严格写入语义。
            return

    def _finish(
        self,
        outcome: StepOutcome,
        *,
        duration_seconds: float | None = None,
        extra_diagnostics: Mapping[str, object] | None = None,
    ) -> None:
        self.state.outcomes[outcome.step_id] = outcome
        self.state.transition(outcome.step_id, outcome.status)
        error_diagnostics = ({
            "error_message": outcome.error.message,
            "exception_type": outcome.error.exception_type,
            "retryable": outcome.error.retryable,
            "error_details": dict(outcome.error.details),
        } if outcome.error else {})
        diagnostics = {
            **dict(outcome.diagnostics),
            **{key: value for key, value in dict(extra_diagnostics or {}).items() if value is not None},
            **error_diagnostics,
        }
        self.context.services.event_sink({
            "timestamp": __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds"),
            "type": "step_state",
            "run_id": self.context.run_id,
            "step_id": outcome.step_id,
            "stage": outcome.step_id.split(".", 1)[0],
            "level": "error" if outcome.status == StepStatus.FAILED else "info",
            "message": f"步骤 {outcome.step_id}：{outcome.status.value}",
            "code": f"step_{outcome.status.value}",
            "status": outcome.status.value,
            "error_code": outcome.error.code if outcome.error else None,
            "cache_reason": diagnostics.get("cache_reason"),
            "duration_seconds": None if duration_seconds is None else round(max(0.0, duration_seconds), 3),
            "capability": outcome.capability,
            "artifacts": [
                {
                    "artifact_id": ref.artifact_id.name,
                    "path": str(ref.path),
                }
                for ref in outcome.artifacts
            ],
            "diagnostics": diagnostics,
        })
