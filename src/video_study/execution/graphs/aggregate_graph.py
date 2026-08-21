from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence


class AggregateGraph:
    """Graph boundary around deterministic-local and authorized-cloud aggregation."""

    @staticmethod
    def node_ids() -> tuple[str, ...]:
        return (
            "aggregate.validate", "aggregate.route",
            "aggregate.run", "aggregate.finalize",
        )

    def run(
        self,
        mode: str,
        results: Sequence[Mapping[str, Any]],
        *,
        execute: Callable[[list[dict[str, Any]]], Mapping[str, Any]],
        cloud_authorized: bool = False,
    ) -> dict[str, Any]:
        from langgraph.graph import END, START, StateGraph

        rows = [dict(row) for row in results]
        full_result: dict[str, Any] = {}

        def project(result: Mapping[str, Any]) -> dict[str, Any]:
            return {key: str(result.get(key, "")) for key in ("video_id", "manifest", "markdown", "docx", "pdf", "mode")}

        def update(state, **changes):
            return {**dict(state), **changes}

        def validate(_state):
            if mode not in {"local", "cloud"}:
                raise ValueError("聚合模式必须是 local 或 cloud")
            if len(rows) < 2:
                raise ValueError("至少需要两个已完成视频才能聚合")
            if mode == "cloud" and not cloud_authorized:
                raise ValueError("云端聚合需要本次云端授权")
            return update(_state, mode=mode, status="running")

        builder = StateGraph(dict)
        builder.add_node("aggregate.validate", validate)
        builder.add_node("aggregate.route", lambda state: update(state, mode=state["mode"]))
        def run_aggregate(state):
            full_result.update(dict(execute(rows)))
            return update(state, aggregate_result_ref=project(full_result))

        builder.add_node("aggregate.run", run_aggregate)
        builder.add_node("aggregate.finalize", lambda state: update(state, status="succeeded"))
        builder.add_edge(START, "aggregate.validate")
        builder.add_edge("aggregate.validate", "aggregate.route")
        builder.add_edge("aggregate.route", "aggregate.run")
        builder.add_edge("aggregate.run", "aggregate.finalize")
        builder.add_edge("aggregate.finalize", END)
        value = builder.compile().invoke({"status": "pending"})
        value["aggregate_result"] = dict(full_result)
        return value
