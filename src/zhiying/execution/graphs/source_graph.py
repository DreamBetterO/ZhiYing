from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping


class SourceGraph:
    """Resolve local/URL inputs into one verified local-source contract."""

    @staticmethod
    def node_ids() -> tuple[str, ...]:
        return (
            "source.local.resolve", "source.url.preflight",
            "source.url.acquire", "source.verify",
        )

    @staticmethod
    def _verify(state: Mapping[str, Any]) -> dict[str, Any]:
        source = dict(state.get("resolved_source") or {})
        path = Path(str(source.get("path") or "")).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"视频不存在：{path}")
        source["path"] = str(path)
        return {**dict(state), "verified_source": source, "status": "succeeded"}

    def run_local(self, path: Path) -> dict[str, Any]:
        from langgraph.graph import END, START, StateGraph

        builder = StateGraph(dict)
        builder.add_node("source.local.resolve", lambda state: {**dict(state),
            "resolved_source": {"kind": "local", "path": str(Path(path).expanduser().resolve())},
        })
        builder.add_node("source.verify", self._verify)
        builder.add_edge(START, "source.local.resolve")
        builder.add_edge("source.local.resolve", "source.verify")
        builder.add_edge("source.verify", END)
        return builder.compile().invoke({"status": "pending"})

    def run_url(
        self,
        url: str,
        *,
        acquire: Callable[[str], Mapping[str, Any]],
    ) -> dict[str, Any]:
        from langgraph.graph import END, START, StateGraph

        def preflight(_state: Mapping[str, Any]) -> dict[str, Any]:
            normalized = str(url or "").strip()
            if not normalized:
                raise ValueError("链接不能为空")
            return {**dict(_state), "source_url": normalized}

        def acquire_node(state: Mapping[str, Any]) -> dict[str, Any]:
            value = dict(acquire(str(state["source_url"])))
            value["kind"] = "url"
            return {**dict(state), "resolved_source": value}

        builder = StateGraph(dict)
        builder.add_node("source.url.preflight", preflight)
        builder.add_node("source.url.acquire", acquire_node)
        builder.add_node("source.verify", self._verify)
        builder.add_edge(START, "source.url.preflight")
        builder.add_edge("source.url.preflight", "source.url.acquire")
        builder.add_edge("source.url.acquire", "source.verify")
        builder.add_edge("source.verify", END)
        return builder.compile().invoke({"status": "pending"})
