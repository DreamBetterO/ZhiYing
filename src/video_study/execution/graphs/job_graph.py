from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence


class JobGraph:
    """Sequential multi-video graph; completed videos survive a later failure."""

    @staticmethod
    def node_ids() -> tuple[str, ...]:
        return (
            "job.validate", "source.resolve", "video.queue.next", "video.run",
            "aggregate.route", "aggregate.run", "job.finalize",
        )

    def run(
        self,
        sources: Sequence[Any],
        *,
        process: Callable[[Any], Mapping[str, Any]],
        aggregate_mode: str = "none",
        aggregate: Callable[[list[dict[str, Any]]], Mapping[str, Any]] | None = None,
        event_sink: Callable[[dict[str, Any]], None] = lambda _event: None,
        checkpoint_adapter=None,
        thread_id: str = "",
        resume: bool = False,
    ) -> dict[str, Any]:
        from langgraph.graph import END, START, StateGraph

        queue = tuple(sources)
        completed: list[dict[str, Any]] = []
        aggregate_full: dict[str, Any] = {}

        def project(result: Mapping[str, Any]) -> dict[str, Any]:
            return {
                key: str(result.get(key, ""))
                for key in ("video_id", "manifest", "markdown", "docx", "pdf", "mode")
            }

        def update(state, **changes):
            return {**dict(state), **changes}

        def validate(_state):
            if not queue:
                raise ValueError("作业至少需要一个视频来源")
            if aggregate_mode not in {"none", "local", "cloud"}:
                raise ValueError("aggregate_mode 必须是 none、local 或 cloud")
            if aggregate_mode != "none" and aggregate is None:
                raise ValueError("聚合作业缺少执行器")
            return update(_state, current_source_index=0, video_result_refs=[], status="running")

        def run_video(state):
            index = int(state["current_source_index"])
            if not completed and state.get("video_result_refs"):
                completed.extend(dict(row) for row in state["video_result_refs"])
            event_sink({"type": "job_video_started", "source_index": index, "total": len(queue)})
            try:
                result = dict(process(queue[index]))
            except BaseException as exc:
                setattr(exc, "completed_results", tuple(completed))
                raise
            completed.append(result)
            event_sink({
                "type": "job_video_completed", "video_id": str(result.get("video_id", "")),
                "current": index + 1, "total": len(queue),
            })
            return update(state, video_result_refs=[project(row) for row in completed], current_source_index=index + 1)

        def queue_route(state):
            return "aggregate.route" if int(state["current_source_index"]) >= len(queue) else "video.run"

        def aggregate_route(_state):
            return "job.finalize" if aggregate_mode == "none" else "aggregate.run"

        def run_aggregate(_state):
            aggregate_full.update(dict(aggregate(list(completed))))
            return update(_state, aggregate_result_ref=project(aggregate_full))

        builder = StateGraph(dict)
        builder.add_node("job.validate", validate)
        builder.add_node("source.resolve", lambda state: update(state, source_count=len(queue)))
        builder.add_node("video.queue.next", lambda state: update(
            state, current_source_index=int(state["current_source_index"]),
        ))
        builder.add_node("video.run", run_video)
        builder.add_node("aggregate.route", lambda state: update(state, aggregate_mode=aggregate_mode))
        builder.add_node("aggregate.run", run_aggregate)
        builder.add_node("job.finalize", lambda state: update(state, status="succeeded"))
        builder.add_edge(START, "job.validate")
        builder.add_edge("job.validate", "source.resolve")
        builder.add_edge("source.resolve", "video.queue.next")
        builder.add_conditional_edges("video.queue.next", queue_route)
        builder.add_edge("video.run", "video.queue.next")
        builder.add_conditional_edges("aggregate.route", aggregate_route)
        builder.add_edge("aggregate.run", "job.finalize")
        builder.add_edge("job.finalize", END)
        graph = builder.compile(checkpointer=checkpoint_adapter.saver if checkpoint_adapter else None)
        config = checkpoint_adapter.config_for(thread_id) if checkpoint_adapter and thread_id else None
        value = graph.invoke(None if resume else {"status": "pending"}, config)
        value["video_results"] = list(completed) if completed else list(value.get("video_result_refs", []))
        if value.get("aggregate_result_ref"):
            value["aggregate_result"] = dict(aggregate_full or value["aggregate_result_ref"])
        return value
