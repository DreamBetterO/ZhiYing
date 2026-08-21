from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .document_v3 import validate_document_v3


def audit_document_v3(document: Mapping[str, Any]) -> dict[str, Any]:
    validate_document_v3(document)
    counts = {"equation": 0, "image": 0, "source_reference": 0, "empty_heading": 0}

    def visit(component: Mapping[str, Any]) -> None:
        kind = str(component.get("type", ""))
        if kind in counts:
            counts[kind] += 1
        if kind == "heading" and not str(component.get("text", "")).strip():
            counts["empty_heading"] += 1
        for child in component.get("children", []):
            visit(child)

    for component in document["components"]:
        visit(component)
    return {"valid": counts["empty_heading"] == 0, **counts}


def audit_visual_evidence(rows: list[Mapping[str, Any]], *, expected: str) -> dict[str, Any]:
    selected = [row for row in rows if row.get("decision") == "select"]
    invalid_selected = [row for row in selected if not row.get("image_path") or not row.get("source_timestamp")]
    valid = not invalid_selected
    if expected == "strong":
        valid = valid and bool(selected)
    elif expected == "weak":
        valid = valid and not selected
    else:
        raise ValueError("expected 必须是 strong 或 weak")
    return {"valid": valid, "selected_count": len(selected), "invalid_selected_count": len(invalid_selected)}


def audit_candidate_index(payload: Mapping[str, Any], *, minimum: int = 1) -> dict[str, Any]:
    candidates = list(payload.get("candidates", []))
    timestamps = [float(row.get("timestamp_seconds", -1)) for row in candidates]
    return {
        "valid": len(candidates) >= minimum and timestamps == sorted(timestamps) and all(value >= 0 for value in timestamps),
        "candidate_count": len(candidates),
    }
