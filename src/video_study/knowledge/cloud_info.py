from __future__ import annotations

from typing import Any

from ..providers import CloudRequestBudget


def _stage(info: dict[str, Any]) -> dict[str, Any]:
    return {"model": str(info.get("model", "")), "attempts": list(info.get("attempts", [])), "usage": dict(info.get("usage", {}))}


def merge_cloud_info(
    plan_info: dict[str, Any], organizer_info: dict[str, Any], budget: CloudRequestBudget | None,
) -> dict[str, Any]:
    successful = [info for info in (plan_info, organizer_info) if info.get("model")]
    if not successful:
        return {}
    merged = dict(organizer_info or plan_info)
    merged["stages"] = {"planning": _stage(plan_info), "organizing": _stage(organizer_info)}
    merged["model"] = " + ".join(dict.fromkeys(str(info["model"]) for info in successful))
    snapshot = budget.snapshot() if budget is not None else {}
    if int(snapshot.get("requests_used", 0)) > 0:
        merged["attempts"] = list(snapshot.get("attempts", []))
        merged["usage"] = dict(snapshot.get("usage", {}))
        merged["request_budget"] = {key: snapshot[key] for key in ("max_requests", "requests_used", "requests_remaining")}
    else:
        attempts: list[dict[str, Any]] = []
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        for stage, info in (("planning", plan_info), ("organizing", organizer_info)):
            attempts.extend({"stage": stage, **dict(item)} for item in info.get("attempts", []))
            for key in usage:
                usage[key] += int(info.get("usage", {}).get(key, 0) or 0)
        merged["attempts"] = attempts
        merged["usage"] = usage
    return merged
