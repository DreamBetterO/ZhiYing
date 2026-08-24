"""V4 可恢复执行内核的稳定公开合同。

P1 只提供独立合同与测试内核；现有 ``pipeline.py`` 尚未切换到这里。
"""

from .artifacts import (
    ArtifactId,
    ArtifactRef,
    ArtifactStore,
    FileArtifactStore,
    LegacyArtifactAdapter,
    WorkspaceCatalog,
    WorkspaceLayout,
    WorkspaceLease,
)
from .cache import (
    CacheDecision, CacheReason, CacheRecord, FileWorkspaceCache,
    LegacyAdoptingWorkspaceCache, WorkspaceCache,
)
from .context import (
    CloudCredentials,
    ProcessingContext,
    ProcessingOptions,
    RunPolicy,
    RuntimeServices,
    VideoSource,
)
from .contracts import (
    ErrorInfo,
    ExecutionCancelled,
    FingerprintMaterial,
    PipelineStep,
    RemoteCost,
    StepOutcome,
    StepSpec,
    StepStatus,
)
from .registry import StepRegistry
from .run_state import GraphRunState

__all__ = [
    "ArtifactId", "ArtifactRef", "ArtifactStore", "CacheDecision", "CacheReason",
    "CacheRecord", "CloudCredentials", "ErrorInfo", "ExecutionCancelled", "FileArtifactStore",
    "FileWorkspaceCache", "LegacyAdoptingWorkspaceCache", "LegacyArtifactAdapter",
    "FingerprintMaterial", "GraphRunState", "PipelineStep",
    "ProcessingContext", "ProcessingOptions", "RemoteCost", "RunPolicy",
    "RuntimeServices", "StepOutcome", "StepRegistry", "StepSpec", "StepStatus",
    "VideoSource", "WorkspaceCache", "WorkspaceCatalog", "WorkspaceLayout", "WorkspaceLease",
]
