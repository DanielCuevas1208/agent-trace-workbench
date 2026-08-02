"""SQLite persistence for portable trace documents."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .models import TraceDocument
from .telemetry import traced_operation

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    agent_version TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    duration_ms REAL NOT NULL,
    source_name TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    ingested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS spans (
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    span_id TEXT NOT NULL,
    parent_span_id TEXT,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    duration_ms REAL NOT NULL,
    sequence_index INTEGER,
    attributes_json TEXT NOT NULL,
    tool_name TEXT,
    arguments_json TEXT,
    result_json TEXT,
    outcome TEXT,
    error TEXT,
    PRIMARY KEY (run_id, span_id)
);

CREATE INDEX IF NOT EXISTS idx_spans_run_sequence ON spans(run_id, sequence_index, start_time);
CREATE INDEX IF NOT EXISTS idx_runs_started_at ON runs(started_at DESC);
"""


class TraceStore:
    """Persist and query traces with one short-lived SQLite connection per operation."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        """Create the local schema when it does not exist."""

        with traced_operation("storage.initialize", {"db.path": self.db_path.name}):
            with self._connect() as connection:
                connection.executescript(SCHEMA)

    def ingest(self, trace: TraceDocument, source_name: str = "local.json") -> dict[str, Any]:
        """Insert or replace one trace and its spans."""

        with traced_operation("storage.ingest", {"run.id": trace.run_id}):
            raw_json = json.dumps(trace.as_jsonable(), sort_keys=True, separators=(",", ":"))
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO runs (
                        run_id, trace_id, agent_name, agent_version, status, started_at,
                        ended_at, duration_ms, source_name, metadata_json, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(run_id) DO UPDATE SET
                        trace_id=excluded.trace_id,
                        agent_name=excluded.agent_name,
                        agent_version=excluded.agent_version,
                        status=excluded.status,
                        started_at=excluded.started_at,
                        ended_at=excluded.ended_at,
                        duration_ms=excluded.duration_ms,
                        source_name=excluded.source_name,
                        metadata_json=excluded.metadata_json,
                        raw_json=excluded.raw_json,
                        ingested_at=CURRENT_TIMESTAMP
                    """,
                    (
                        trace.run_id,
                        trace.trace_id,
                        trace.agent_name,
                        trace.agent_version,
                        trace.status,
                        trace.started_at.isoformat(),
                        trace.ended_at.isoformat(),
                        trace.duration_ms,
                        source_name,
                        _json(trace.metadata),
                        raw_json,
                    ),
                )
                connection.execute("DELETE FROM spans WHERE run_id = ?", (trace.run_id,))
                for span in trace.ordered_spans():
                    tool = span.tool_call
                    connection.execute(
                        """
                        INSERT INTO spans (
                            run_id, span_id, parent_span_id, name, kind, status, start_time,
                            end_time, duration_ms, sequence_index, attributes_json, tool_name,
                            arguments_json, result_json, outcome, error
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            trace.run_id,
                            span.span_id,
                            span.parent_span_id,
                            span.name,
                            span.kind,
                            span.status,
                            span.start_time.isoformat(),
                            span.end_time.isoformat(),
                            span.duration_ms,
                            span.sequence,
                            _json(span.attributes),
                            tool.name if tool else None,
                            _json(tool.arguments) if tool else None,
                            _json(tool.result) if tool else None,
                            tool.outcome if tool else None,
                            tool.error if tool else None,
                        ),
                    )
            return self.get_run(trace.run_id) or {}

    def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return recent run summaries."""

        safe_limit = max(1, min(limit, 100))
        with traced_operation("storage.list_runs", {"run.limit": safe_limit}):
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT * FROM runs ORDER BY started_at DESC, run_id DESC LIMIT ?",
                    (safe_limit,),
                ).fetchall()
                summaries = [_run_row(row) for row in rows]
                for summary in summaries:
                    tool_row = connection.execute(
                        "SELECT COUNT(*) AS count FROM spans WHERE run_id = ? AND kind = 'tool'",
                        (summary["run_id"],),
                    ).fetchone()
                    summary["tool_count"] = tool_row["count"]
            return summaries

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        """Return one run with its spans."""

        with traced_operation("storage.get_run", {"run.id": run_id}):
            with self._connect() as connection:
                run = connection.execute(
                    "SELECT * FROM runs WHERE run_id = ?", (run_id,)
                ).fetchone()
                if run is None:
                    return None
                spans = connection.execute(
                    """
                    SELECT * FROM spans
                    WHERE run_id = ?
                    ORDER BY sequence_index IS NULL, sequence_index, start_time, span_id
                    """,
                    (run_id,),
                ).fetchall()
            result = _run_row(run)
            result["spans"] = [_span_row(row) for row in spans]
            result["tool_count"] = sum(span["kind"] == "tool" for span in result["spans"])
            return result

    def get_trace(self, run_id: str) -> TraceDocument | None:
        """Load the original trace contract for replay."""

        with self._connect() as connection:
            row = connection.execute(
                "SELECT raw_json FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        return TraceDocument.model_validate_json(row["raw_json"]) if row else None


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _run_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "run_id": row["run_id"],
        "trace_id": row["trace_id"],
        "agent_name": row["agent_name"],
        "agent_version": row["agent_version"],
        "status": row["status"],
        "started_at": row["started_at"],
        "ended_at": row["ended_at"],
        "duration_ms": row["duration_ms"],
        "source_name": row["source_name"],
        "metadata": json.loads(row["metadata_json"]),
        "ingested_at": row["ingested_at"],
    }


def _span_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "span_id": row["span_id"],
        "parent_span_id": row["parent_span_id"],
        "name": row["name"],
        "kind": row["kind"],
        "status": row["status"],
        "start_time": row["start_time"],
        "end_time": row["end_time"],
        "duration_ms": row["duration_ms"],
        "sequence": row["sequence_index"],
        "attributes": json.loads(row["attributes_json"]),
        "tool_call": {
            "name": row["tool_name"],
            "arguments": json.loads(row["arguments_json"]),
            "result": json.loads(row["result_json"]),
            "outcome": row["outcome"],
            "error": row["error"],
        }
        if row["tool_name"]
        else None,
    }


