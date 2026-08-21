"""Serializable, bounded state projections for P2 LangGraph checkpoints."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .artifacts import ArtifactId, ArtifactRef
from .contracts import ErrorInfo, StepOutcome, StepStatus


def outcome_projection(outcome: StepOutcome) -> dict[str, Any]:
    """Keep references and stable diagnostics only; never persist runtime objects."""
    return {
        "status": outcome.status.value,
        "capability": outcome.capability,
        "artifacts": [
            {"artifact_id": ref.artifact_id.name, "path": str(ref.path), "digest": ref.digest}
            for ref in outcome.artifacts
        ],
        "error": None if outcome.error is None else {
            "code": outcome.error.code,
            "message": outcome.error.message[:500],
            "exception_type": outcome.error.exception_type,
            "retryable": outcome.error.retryable,
        },
        "diagnostics": {
            str(key): value for key, value in outcome.diagnostics.items()
            if str(key).lower() not in {"api_key", "authorization", "credentials", "traceback"}
        },
    }


def initial_video_state(step_ids: tuple[str, ...]) -> dict[str, Any]:
    return {"schema_version": 1, "statuses": {step_id: "pending" for step_id in step_ids}, "outcomes": {}}


def outcome_from_projection(
    step_id: str,
    run_id: str,
    value: Mapping[str, Any],
    artifact_ids: Mapping[str, ArtifactId],
) -> StepOutcome:
    refs = []
    for item in value.get("artifacts", []):
        artifact_name = str(item.get("artifact_id") or "")
        artifact_id = artifact_ids.get(artifact_name)
        if artifact_id is None:
            raise ValueError(f"UNKNOWN_CHECKPOINT_ARTIFACT：{artifact_name}")
        refs.append(ArtifactRef(
            artifact_id,
            Path(str(item["path"])),
            str(item.get("digest") or ""),
        ))
    error_value = value.get("error")
    error = None if not error_value else ErrorInfo(
        str(error_value.get("code") or "GRAPH_ERROR"),
        str(error_value.get("message") or ""),
        str(error_value.get("exception_type") or ""),
        bool(error_value.get("retryable")),
    )
    return StepOutcome(
        step_id,
        run_id,
        StepStatus(str(value["status"])),
        str(value.get("capability") or "offline"),
        tuple(refs),
        error,
        value.get("diagnostics", {}),
    )
