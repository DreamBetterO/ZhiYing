from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from .contracts import StepOutcome, StepStatus


@dataclass
class GraphRunState:
    """In-memory projection returned by GraphRuntime compatibility façades."""

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
