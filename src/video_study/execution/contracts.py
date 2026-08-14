from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Mapping, Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from .artifacts import ArtifactId, ArtifactRef
    from .context import ProcessingContext


class StepStatus(str, Enum):
    PENDING = "pending"
    CHECKING_CACHE = "checking_cache"
    CACHED = "cached"
    READY = "ready"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    DEGRADED = "degraded"
    SKIPPED = "skipped"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RemoteCost(str, Enum):
    NONE = "none"
    LOCAL_HEAVY = "local_heavy"
    CLOUD = "cloud"


class ExecutionCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class ErrorInfo:
    code: str
    message: str
    exception_type: str = ""
    retryable: bool = False
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "details", MappingProxyType(dict(self.details)))


@dataclass(frozen=True)
class FingerprintMaterial:
    components: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "components", MappingProxyType(dict(self.components)))


@dataclass(frozen=True)
class StepSpec:
    step_id: str
    implementation_version: int
    dependencies: tuple[str, ...] = ()
    inputs: tuple["ArtifactId", ...] = ()
    outputs: tuple["ArtifactId", ...] = ()
    config_keys: tuple[str, ...] = ()
    remote_cost: RemoteCost = RemoteCost.NONE
    capabilities: tuple[str, ...] = ("offline",)
    degradation_policy: str = "fail"
    owner: str = ""
    tests: tuple[str, ...] = ()
    error_code_prefix: str = "STEP"
    contract_version: str = "1"

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_.-]*", self.step_id):
            raise ValueError("step_id 必须是非空稳定标识")
        if self.implementation_version < 1:
            raise ValueError("implementation_version 必须大于 0")
        if not self.error_code_prefix:
            raise ValueError("error_code_prefix 不能为空")
        object.__setattr__(self, "dependencies", tuple(dict.fromkeys(self.dependencies)))
        object.__setattr__(self, "inputs", tuple(self.inputs))
        object.__setattr__(self, "outputs", tuple(self.outputs))


@dataclass(frozen=True)
class StepOutcome:
    step_id: str
    run_id: str
    status: StepStatus
    capability: str = "offline"
    artifacts: tuple["ArtifactRef", ...] = ()
    error: ErrorInfo | None = None
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "artifacts", tuple(self.artifacts))
        object.__setattr__(self, "diagnostics", MappingProxyType(dict(self.diagnostics)))
        if self.status in {StepStatus.FAILED, StepStatus.CANCELLED} and self.error is None:
            raise ValueError(f"{self.status.value} outcome 必须包含 ErrorInfo")


class PipelineStep(Protocol):
    spec: StepSpec

    def fingerprint(
        self,
        context: "ProcessingContext",
        inputs: Mapping["ArtifactId", "ArtifactRef"],
    ) -> FingerprintMaterial: ...

    def execute(
        self,
        context: "ProcessingContext",
        inputs: Mapping["ArtifactId", "ArtifactRef"],
        staging_dir: Path,
    ) -> StepOutcome: ...

    def validate(self, context: "ProcessingContext", outcome: StepOutcome) -> None: ...
