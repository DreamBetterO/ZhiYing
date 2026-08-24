from __future__ import annotations

from typing import Any, Callable, Mapping

from ..decision_policy import VisualNeedLevel


class VisualGraph:
    """Nested visual workflow with deterministic routing and one evidence factory."""

    @staticmethod
    def node_ids() -> tuple[str, ...]:
        return ("visual.profile", "visual.plan", "visual.evidence", "visual.finalize")

    def run(
        self,
        need: VisualNeedLevel,
        *,
        execute: Callable[[], Any],
    ) -> dict[str, Any]:
        from langgraph.graph import END, START, StateGraph

        def update(state: Mapping[str, Any], **changes) -> dict[str, Any]:
            return {**dict(state), **changes}

        builder = StateGraph(dict)
        builder.add_node("visual.profile", lambda state: update(state, visual_need=need.value))
        builder.add_node("visual.plan", lambda state: update(state, provider_required=need != VisualNeedLevel.NONE))
        builder.add_node("visual.evidence", lambda state: update(state, evidence=execute()))
        builder.add_node("visual.finalize", lambda state: update(state, status="succeeded"))
        builder.add_edge(START, "visual.profile")
        builder.add_edge("visual.profile", "visual.plan")
        builder.add_edge("visual.plan", "visual.evidence")
        builder.add_edge("visual.evidence", "visual.finalize")
        builder.add_edge("visual.finalize", END)
        return builder.compile().invoke({"status": "pending"})
