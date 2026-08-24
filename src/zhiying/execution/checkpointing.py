"""Version-bound local SQLite checkpoint adapter for P2 graph comparison runs."""

from __future__ import annotations

import sqlite3
from pathlib import Path


class SqliteCheckpointAdapter:
    def __init__(self, database: Path, graph_version: str) -> None:
        from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
        from langgraph.checkpoint.sqlite import SqliteSaver

        self.graph_version = graph_version
        self.connection = sqlite3.connect(database, check_same_thread=False)
        self.saver = SqliteSaver(self.connection, serde=JsonPlusSerializer(allowed_msgpack_modules=[]))
        self.connection.execute("CREATE TABLE IF NOT EXISTS zhiying_graph_threads (thread_id TEXT PRIMARY KEY, graph_version TEXT NOT NULL)")
        self.connection.commit()

    def config_for(self, thread_id: str) -> dict:
        row = self.connection.execute("SELECT graph_version FROM zhiying_graph_threads WHERE thread_id = ?", (thread_id,)).fetchone()
        if row is not None and row[0] != self.graph_version:
            raise ValueError("GRAPH_VERSION_MISMATCH：必须创建新 thread，旧 checkpoint 不可转换")
        if row is None:
            self.connection.execute("INSERT INTO zhiying_graph_threads VALUES (?, ?)", (thread_id, self.graph_version))
            self.connection.commit()
        return {"configurable": {"thread_id": thread_id}}

    def close(self) -> None:
        self.connection.close()
