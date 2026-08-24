"""Bounded terminal event projection for GraphRuntime."""

from __future__ import annotations

from typing import Mapping

from .context import ProcessingContext
from .contracts import StepOutcome, StepStatus


def emit_terminal_event(
    context: ProcessingContext,
    outcome: StepOutcome,
    *,
    duration_seconds: float | None = None,
    extra_diagnostics: Mapping[str, object] | None = None,
) -> None:
    error = outcome.error
    diagnostics = {
        **dict(outcome.diagnostics),
        **{
            key: value
            for key, value in dict(extra_diagnostics or {}).items()
            if value is not None
        },
    }
    if error:
        diagnostics.update({
            "error_message": error.message,
            "exception_type": error.exception_type,
            "retryable": error.retryable,
            "error_details": dict(error.details),
        })
    context.services.event_sink({
        "timestamp": __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds"),
        "type": "step_state",
        "run_id": context.run_id,
        "job_id": context.run_id,
        "thread_id": context.run_id,
        "checkpoint_id": "",
        "graph_version": "v6.1-editorial-tools-1",
        "node_id": outcome.step_id,
        "task_id": f"video:{context.source.video_id}:{outcome.step_id}",
        "attempt_id": f"{context.run_id}:{outcome.step_id}",
        "step_id": outcome.step_id,
        "stage": outcome.step_id.split(".", 1)[0],
        "level": "error" if outcome.status == StepStatus.FAILED else "info",
        "message": f"步骤 {outcome.step_id}：{outcome.status.value}",
        "code": f"step_{outcome.status.value}",
        "status": outcome.status.value,
        "error_code": error.code if error else None,
        "cache_reason": diagnostics.get("cache_reason"),
        "duration_seconds": (
            None if duration_seconds is None else round(max(0.0, duration_seconds), 3)
        ),
        "capability": outcome.capability,
        "input_artifact_ids": [],
        "output_artifact_ids": [ref.artifact_id.name for ref in outcome.artifacts],
        "duration": None if duration_seconds is None else round(max(0.0, duration_seconds), 3),
        "cost_summary": {},
        "artifacts": [
            {"artifact_id": ref.artifact_id.name, "path": str(ref.path)}
            for ref in outcome.artifacts
        ],
        "diagnostics": diagnostics,
    })
