"""P2 LangGraph boundary. It is intentionally not a production entrypoint yet."""

from __future__ import annotations

from typing import Any, Mapping


class GraphRuntime:
    """Owns production Graph compilation and execution."""

    @staticmethod
    def production_enabled() -> bool:
        return True

    @staticmethod
    def compile_linear_graph(node_ids: tuple[str, ...]):
        """Compile a deterministic test graph without runtime services in state."""
        from langgraph.graph import END, START, StateGraph

        builder = StateGraph(dict)
        for node_id in node_ids:
            builder.add_node(node_id, lambda state: dict(state))
        if not node_ids:
            builder.add_edge(START, END)
        else:
            builder.add_edge(START, node_ids[0])
            for previous, current in zip(node_ids, node_ids[1:]):
                builder.add_edge(previous, current)
            builder.add_edge(node_ids[-1], END)
        return builder.compile()

    @classmethod
    def topology(cls, specs) -> tuple[str, ...]:
        return tuple(spec.step_id for spec in specs)

    def run_single_video(self, kernel, *, checkpoint_adapter=None, thread_id: str = "", resume: bool = False, interrupt_after: tuple[str, ...] = ()):
        """Run the current registered DAG as a P2 comparison graph.

        The graph state stores only status strings and Artifact references; runtime
        services remain captured by the composition root and never enter state.
        """
        from langgraph.graph import END, START, StateGraph

        from .contracts import ErrorInfo, StepOutcome, StepStatus
        from .graph_state import outcome_from_projection, outcome_projection
        from .node_executor import NodeExecutor
        from .run_state import GraphRunState
        from .terminal_events import emit_terminal_event

        order = kernel.registry.required_order(kernel.context.policy.target_steps or None)
        executor = NodeExecutor(kernel.context, kernel.artifacts, kernel.cache)
        runtime_outcomes: dict[str, StepOutcome] = {}
        artifact_ids = {artifact.name: artifact for step_id in order for artifact in (*kernel.registry.get(step_id).spec.inputs, *kernel.registry.get(step_id).spec.outputs)}

        def rehydrate(serialized: Mapping[str, Any]) -> None:
            for step_id, row in serialized.items():
                if step_id in runtime_outcomes:
                    continue
                runtime_outcomes[step_id] = outcome_from_projection(
                    step_id, kernel.context.run_id, row, artifact_ids,
                )

        def node(step_id: str):
            def execute(state: Mapping[str, Any]) -> dict[str, Any]:
                rehydrate(state.get("outcomes", {}))
                statuses = {key: StepStatus(value) for key, value in state["statuses"].items()}
                outcomes = dict(runtime_outcomes)
                run_state = GraphRunState(kernel.context.run_id, statuses, outcomes)
                step = kernel.registry.get(step_id)
                blocked = [dependency for dependency in step.spec.dependencies if run_state.statuses.get(dependency) in {StepStatus.FAILED, StepStatus.CANCELLED, StepStatus.SKIPPED}]
                if blocked:
                    outcome = StepOutcome(step_id, kernel.context.run_id, StepStatus.SKIPPED, diagnostics={"blocked_by": blocked})
                    cache_reason = None
                    duration_seconds = None
                elif kernel.context.services.cancelled() or StepStatus.CANCELLED in run_state.statuses.values():
                    outcome = StepOutcome(step_id, kernel.context.run_id, StepStatus.CANCELLED, error=ErrorInfo("RUN_CANCELLED", "任务已取消", "ExecutionCancelled"))
                    cache_reason = None
                    duration_seconds = None
                else:
                    inputs = {}
                    for dependency in step.spec.dependencies:
                        for ref in run_state.outcomes[dependency].artifacts:
                            inputs[ref.artifact_id] = ref
                    result = executor.run(step, inputs, run_state.transition)
                    outcome = result.outcome
                    cache_reason = result.cache_reason
                    duration_seconds = result.duration_seconds
                run_state.outcomes[step_id] = outcome
                runtime_outcomes[step_id] = outcome
                if run_state.statuses.get(step_id) != outcome.status:
                    run_state.transition(step_id, outcome.status)
                emit_terminal_event(
                    kernel.context,
                    outcome,
                    duration_seconds=duration_seconds,
                    extra_diagnostics={"cache_reason": cache_reason},
                )
                return {"statuses": {key: value.value for key, value in run_state.statuses.items()}, "outcomes": {key: outcome_projection(value) for key, value in runtime_outcomes.items()}}
            return execute

        builder = StateGraph(dict)
        for step_id in order:
            builder.add_node(step_id, node(step_id))
        builder.add_edge(START, order[0])
        for previous, current in zip(order, order[1:]):
            builder.add_edge(previous, current)
        builder.add_edge(order[-1], END)
        initial = None if resume else {"statuses": {step_id: StepStatus.PENDING.value for step_id in order}, "outcomes": {}}
        graph = builder.compile(checkpointer=checkpoint_adapter.saver if checkpoint_adapter else None, interrupt_after=list(interrupt_after))
        config = checkpoint_adapter.config_for(thread_id) if checkpoint_adapter and thread_id else None
        return graph.invoke(initial, config)

    def run_compatible_state(self, kernel, **kwargs):
        """Return the legacy state shape for P2 differential tests only."""
        from .graph_state import outcome_from_projection
        from .run_state import GraphRunState

        value = self.run_single_video(kernel, **kwargs)
        order = kernel.registry.required_order(
            kernel.context.policy.target_steps or None
        )
        artifact_ids = {
            artifact.name: artifact
            for step_id in order
            for artifact in (
                *kernel.registry.get(step_id).spec.inputs,
                *kernel.registry.get(step_id).spec.outputs,
            )
        }
        outcomes = {
            step_id: outcome_from_projection(
                step_id,
                kernel.context.run_id,
                projection,
                artifact_ids,
            )
            for step_id, projection in value["outcomes"].items()
        }
        return GraphRunState(
            kernel.context.run_id,
            {step_id: outcome.status for step_id, outcome in outcomes.items()},
            outcomes,
        )
