"""SQLite persistence for portable trace documents.

The store coordinates local readers and writers on one database file.
It enables the WAL journal so readers keep a committed snapshot while a
writer is active. It sets a busy timeout so writers wait for the write
lock instead of failing on first contact.

The store also keeps local review annotations beside each run. A label
and a note stay in the runs table. They survive re-ingestion and never
enter the portable trace contract. Each run keeps the folder that
produced it, so the report layer can group evidence by source directory.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, TypeVar
from uuid import uuid4

from .models import TraceDocument
from .telemetry import traced_operation

_T = TypeVar("_T")

_SYNCHRONOUS_LABELS = {0: "off", 1: "normal", 2: "full", 3: "extra"}
_LOCK_ERROR_HINT = "database is locked"

_ANNOTATION_MAX = {"label": 80, "note": 2000}
_EXTRA_COLUMNS = {
    "label": "label TEXT NOT NULL DEFAULT ''",
    "note": "note TEXT NOT NULL DEFAULT ''",
    "source_dir": "source_dir TEXT NOT NULL DEFAULT ''",
}
_EMPTY_FOLDER = "api"

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
    source_dir TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    ingested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    label TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT ''
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

CREATE TABLE IF NOT EXISTS comparisons (
    comparison_id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    run_a TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    run_b TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    report_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class TraceStore:
    """Persist and query traces with one short-lived SQLite connection per operation."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        busy_timeout_ms: int = 5000,
    ) -> None:
        if busy_timeout_ms < 0:
            raise ValueError("busy_timeout_ms must not be negative")
        self.db_path = Path(db_path)
        self.busy_timeout_ms = busy_timeout_ms
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=self.busy_timeout_ms / 1000)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute(f"PRAGMA busy_timeout = {int(self.busy_timeout_ms)}")
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    def initialize(self) -> None:
        """Create the local schema when it does not exist."""

        with traced_operation("storage.initialize", {"db.path": self.db_path.name}):
            with self._connect() as connection:
                connection.executescript(SCHEMA)
            self._ensure_extra_columns()

    def _ensure_extra_columns(self) -> None:
        """Add local columns to runs tables created before release 1.0.

        A database from an earlier release has a runs table without the
        label, note, and source directory columns. This migration extends
        that table in place so existing evidence stays readable. Two
        processes may run the migration at once, so a duplicate column
        error counts as done.
        """

        with self._connect() as connection:
            existing = {
                row["name"] for row in connection.execute("PRAGMA table_info(runs)")
            }
            for column, definition in _EXTRA_COLUMNS.items():
                if column in existing:
                    continue
                try:
                    connection.execute(f"ALTER TABLE runs ADD COLUMN {definition}")
                except sqlite3.OperationalError as error:
                    if "duplicate column" not in str(error).lower():
                        raise

    def store_info(self) -> dict[str, Any]:
        """Return the concurrency settings of the local database."""

        with traced_operation("storage.store_info", {"db.path": self.db_path.name}):
            with self._connect() as connection:
                journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
                busy_timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]
                synchronous = connection.execute("PRAGMA synchronous").fetchone()[0]
                sqlite_version = connection.execute("SELECT sqlite_version()").fetchone()[0]
            return {
                "db_path": str(self.db_path),
                "journal_mode": journal_mode,
                "busy_timeout_ms": busy_timeout,
                "synchronous": _SYNCHRONOUS_LABELS.get(synchronous, str(synchronous)),
                "sqlite_version": sqlite_version,
            }

    def ingest(
        self,
        trace: TraceDocument,
        source_name: str = "local.json",
        source_dir: str = "",
    ) -> dict[str, Any]:
        """Insert or replace one trace and its spans.

        The source directory records where the trace came from. The
        report layer groups evidence by this folder. A re-ingest keeps
        the latest provenance, like the source file name does.
        """

        with traced_operation("storage.ingest", {"run.id": trace.run_id}):
            _retry_on_lock(lambda: self._write_trace(trace, source_name, source_dir))
            return self.get_run(trace.run_id) or {}

    def _write_trace(self, trace: TraceDocument, source_name: str, source_dir: str) -> None:
        raw_json = json.dumps(trace.as_jsonable(), sort_keys=True, separators=(",", ":"))
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO runs (
                    run_id, trace_id, agent_name, agent_version, status, started_at,
                    ended_at, duration_ms, source_name, source_dir, metadata_json, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    trace_id=excluded.trace_id,
                    agent_name=excluded.agent_name,
                    agent_version=excluded.agent_version,
                    status=excluded.status,
                    started_at=excluded.started_at,
                    ended_at=excluded.ended_at,
                    duration_ms=excluded.duration_ms,
                    source_name=excluded.source_name,
                    source_dir=excluded.source_dir,
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
                    source_dir,
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

    def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return recent run summaries."""

        safe_limit = max(1, min(limit, 100))
        with traced_operation("storage.list_runs", {"run.limit": safe_limit}):
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT * FROM runs ORDER BY started_at DESC, run_id DESC LIMIT ?",
                    (safe_limit,),
                ).fetchall()
                return _summarize_runs(connection, rows)

    def list_run_ids(self) -> list[str]:
        """Return every stored run ID in deterministic order."""

        with traced_operation("storage.list_run_ids", {"db.path": self.db_path.name}):
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT run_id FROM runs ORDER BY started_at DESC, run_id DESC"
                ).fetchall()
                return [row["run_id"] for row in rows]

    def search_runs(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Return run summaries matching a text query across runs and spans."""

        safe_limit = max(1, min(limit, 100))
        term = query.strip()
        if not term:
            return []
        pattern = f"%{_escape_like(term)}%"
        with traced_operation("storage.search_runs", {"run.query": term}):
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT DISTINCT r.*
                    FROM runs r
                    LEFT JOIN spans s ON s.run_id = r.run_id
                    WHERE r.run_id LIKE ? ESCAPE '!'
                       OR r.trace_id LIKE ? ESCAPE '!'
                       OR r.agent_name LIKE ? ESCAPE '!'
                       OR r.source_name LIKE ? ESCAPE '!'
                       OR r.label LIKE ? ESCAPE '!'
                       OR s.name LIKE ? ESCAPE '!'
                       OR s.tool_name LIKE ? ESCAPE '!'
                    ORDER BY r.started_at DESC, r.run_id DESC
                    LIMIT ?
                    """,
                    (pattern, pattern, pattern, pattern, pattern, pattern, pattern, safe_limit),
                ).fetchall()
                return _summarize_runs(connection, rows)

    def unreviewed_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return run summaries that have no review label.

        A reviewer starts here. Each returned run has an empty label, so
        it has not been triaged yet. The review page links each run to its
        annotation form.
        """

        safe_limit = max(1, min(limit, 100))
        with traced_operation("storage.unreviewed_runs", {"review.limit": safe_limit}):
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT * FROM runs WHERE label = '' "
                    "ORDER BY started_at DESC, run_id DESC LIMIT ?",
                    (safe_limit,),
                ).fetchall()
                return _summarize_runs(connection, rows)

    def library_report(self) -> dict[str, Any]:
        """Return a folder-level summary of the local trace library.

        The report shows one row per agent and one row per source folder.
        It also shows library-wide totals for runs, tool calls, failures,
        and labeled evidence. The data never leaves the local database.
        """

        with traced_operation("storage.library_report", {"db.path": self.db_path.name}):
            with self._connect() as connection:
                total = connection.execute(
                    """
                    SELECT
                        COUNT(*) AS runs,
                        COALESCE(SUM(status = 'error'), 0) AS failure_runs,
                        COALESCE(SUM(status = 'ok'), 0) AS ok_runs,
                        COALESCE(SUM(label = ''), 0) AS unlabeled_runs,
                        COALESCE(SUM(label != ''), 0) AS labeled_runs,
                        COALESCE(SUM(duration_ms), 0) AS total_duration_ms,
                        COUNT(DISTINCT agent_name) AS agents
                    FROM runs
                    """
                ).fetchone()
                tool_calls = connection.execute(
                    "SELECT COUNT(*) AS count FROM spans WHERE kind = 'tool'"
                ).fetchone()["count"]
                agent_rows = connection.execute(
                    """
                    SELECT agent_name,
                        COUNT(*) AS runs,
                        COALESCE(SUM(status = 'error'), 0) AS failure_runs,
                        COALESCE(SUM(label = ''), 0) AS unlabeled_runs,
                        ROUND(AVG(duration_ms), 3) AS avg_duration_ms
                    FROM runs
                    GROUP BY agent_name
                    ORDER BY runs DESC, agent_name ASC
                    """
                ).fetchall()
                source_rows = connection.execute(
                    """
                    SELECT source_dir,
                        COUNT(*) AS runs,
                        COALESCE(SUM(status = 'error'), 0) AS failure_runs,
                        COALESCE(SUM(label = ''), 0) AS unlabeled_runs
                    FROM runs
                    GROUP BY source_dir
                    ORDER BY runs DESC, source_dir ASC
                    """
                ).fetchall()
                agent_tools = {
                    row["agent_name"]: row["tool_calls"]
                    for row in connection.execute(
                        """
                        SELECT r.agent_name, COUNT(*) AS tool_calls
                        FROM spans s JOIN runs r ON r.run_id = s.run_id
                        WHERE s.kind = 'tool'
                        GROUP BY r.agent_name
                        """
                    ).fetchall()
                }
                source_tools = {
                    _folder_name(row["source_dir"]): row["tool_calls"]
                    for row in connection.execute(
                        """
                        SELECT r.source_dir, COUNT(*) AS tool_calls
                        FROM spans s JOIN runs r ON r.run_id = s.run_id
                        WHERE s.kind = 'tool'
                        GROUP BY r.source_dir
                        """
                    ).fetchall()
                }
                source_agents = {
                    _folder_name(row["source_dir"]): row["agents"]
                    for row in connection.execute(
                        """
                        SELECT source_dir, COUNT(DISTINCT agent_name) AS agents
                        FROM runs
                        GROUP BY source_dir
                        """
                    ).fetchall()
                }
        by_agent = [
            {
                "agent_name": row["agent_name"],
                "runs": row["runs"],
                "failure_runs": row["failure_runs"],
                "unlabeled_runs": row["unlabeled_runs"],
                "tool_calls": agent_tools.get(row["agent_name"], 0),
                "avg_duration_ms": row["avg_duration_ms"],
            }
            for row in agent_rows
        ]
        by_source = []
        for row in source_rows:
            folder = _folder_name(row["source_dir"])
            by_source.append(
                {
                    "source_dir": folder,
                    "runs": row["runs"],
                    "failure_runs": row["failure_runs"],
                    "unlabeled_runs": row["unlabeled_runs"],
                    "tool_calls": source_tools.get(folder, 0),
                    "agents": source_agents.get(folder, 0),
                }
            )
        by_source.sort(key=lambda item: (-item["runs"], item["source_dir"]))
        return {
            "totals": {
                "runs": total["runs"],
                "ok_runs": total["ok_runs"],
                "failure_runs": total["failure_runs"],
                "labeled_runs": total["labeled_runs"],
                "unlabeled_runs": total["unlabeled_runs"],
                "tool_calls": tool_calls,
                "agents": total["agents"],
                "sources": len(by_source),
                "total_duration_ms": round(total["total_duration_ms"], 3),
            },
            "by_agent": by_agent,
            "by_source": by_source,
        }

    def get_run(
        self,
        run_id: str,
        *,
        span_kind: str | None = None,
        span_status: str | None = None,
        span_tool: str | None = None,
    ) -> dict[str, Any] | None:
        """Return one run with optional span filtering."""

        with traced_operation("storage.get_run", {"run.id": run_id}):
            with self._connect() as connection:
                run = connection.execute(
                    "SELECT * FROM runs WHERE run_id = ?", (run_id,)
                ).fetchone()
                if run is None:
                    return None
                tool_count = connection.execute(
                    "SELECT COUNT(*) AS count FROM spans WHERE run_id = ? AND kind = 'tool'",
                    (run_id,),
                ).fetchone()["count"]
                query = "SELECT * FROM spans WHERE run_id = ?"
                params: list[Any] = [run_id]
                if span_kind:
                    query += " AND kind = ?"
                    params.append(span_kind)
                if span_status:
                    query += " AND status = ?"
                    params.append(span_status)
                if span_tool:
                    query += " AND tool_name = ?"
                    params.append(span_tool)
                query += (
                    " ORDER BY sequence_index IS NULL, sequence_index, start_time, span_id"
                )
                spans = connection.execute(query, params).fetchall()
            result = _run_row(run)
            result["spans"] = [_span_row(row) for row in spans]
            result["tool_count"] = tool_count
            return result

    def get_trace(self, run_id: str) -> TraceDocument | None:
        """Load the original trace contract for replay."""

        with self._connect() as connection:
            row = connection.execute(
                "SELECT raw_json FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        return TraceDocument.model_validate_json(row["raw_json"]) if row else None

    def update_annotations(
        self,
        run_id: str,
        *,
        label: str | None = None,
        note: str | None = None,
    ) -> dict[str, Any] | None:
        """Set the local label and review notes for one run.

        A None value leaves the current annotation untouched. An empty
        string clears it. The method returns the updated run, or None when
        the run does not exist.
        """

        if label is None and note is None:
            raise ValueError("Provide a label, a note, or both")
        for name, value in (("label", label), ("note", note)):
            if value is not None and len(value) > _ANNOTATION_MAX[name]:
                raise ValueError(f"{name} must be at most {_ANNOTATION_MAX[name]} characters")
        with traced_operation("storage.update_annotations", {"run.id": run_id}):

            def _update() -> bool:
                with self._connect() as connection:
                    sets: list[str] = []
                    values: list[Any] = []
                    for column, value in (("label", label), ("note", note)):
                        if value is not None:
                            sets.append(f"{column} = ?")
                            values.append(value)
                    values.append(run_id)
                    cursor = connection.execute(
                        f"UPDATE runs SET {', '.join(sets)} WHERE run_id = ?", values
                    )
                    return cursor.rowcount > 0

            if not _retry_on_lock(_update):
                return None
            return self.get_run(run_id)

    def save_comparison(
        self,
        run_a: str,
        run_b: str,
        label: str,
        report: dict[str, Any],
    ) -> dict[str, Any]:
        """Store a comparison report and return the saved record."""

        comparison_id = uuid4().hex[:12]
        with traced_operation("storage.save_comparison", {"run.a": run_a, "run.b": run_b}):
            def _write() -> None:
                with self._connect() as connection:
                    connection.execute(
                        """
                        INSERT INTO comparisons (comparison_id, label, run_a, run_b, report_json)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (comparison_id, label, run_a, run_b, _json(report)),
                    )

            _retry_on_lock(_write)
            return self.get_comparison(comparison_id)

    def list_comparisons(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return recent saved comparison summaries."""

        safe_limit = max(1, min(limit, 100))
        with traced_operation("storage.list_comparisons", {"comparison.limit": safe_limit}):
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT * FROM comparisons
                    ORDER BY created_at DESC, comparison_id DESC
                    LIMIT ?
                    """,
                    (safe_limit,),
                ).fetchall()
                return [_comparison_row(row) for row in rows]

    def get_comparison(self, comparison_id: str) -> dict[str, Any] | None:
        """Return one saved comparison with its report."""

        with traced_operation("storage.get_comparison", {"comparison.id": comparison_id}):
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM comparisons WHERE comparison_id = ?",
                    (comparison_id,),
                ).fetchone()
            return _comparison_row(row) if row else None

    def delete_comparison(self, comparison_id: str) -> bool:
        """Remove one saved comparison and report whether it existed."""

        with traced_operation("storage.delete_comparison", {"comparison.id": comparison_id}):
            def _delete() -> bool:
                with self._connect() as connection:
                    cursor = connection.execute(
                        "DELETE FROM comparisons WHERE comparison_id = ?",
                        (comparison_id,),
                    )
                return cursor.rowcount > 0

            return _retry_on_lock(_delete)


def _retry_on_lock(operation: Callable[[], _T], *, attempts: int = 3) -> _T:
    """Run a write operation, retrying when the database reports a lock.

    A transient "database is locked" error is safe to retry because the
    operation starts its own transaction on a fresh connection. Other
    errors pass through unchanged.
    """

    last_error: sqlite3.OperationalError | None = None
    for attempt in range(attempts):
        try:
            return operation()
        except sqlite3.OperationalError as error:
            last_error = error
            if _LOCK_ERROR_HINT not in str(error):
                raise
            if attempt < attempts - 1:
                time.sleep(0.05)
    assert last_error is not None
    raise last_error


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _escape_like(value: str) -> str:
    return value.replace("!", "!!").replace("%", "!%").replace("_", "!_")


def _folder_name(source_dir: str) -> str:
    """Return a display name for the folder that produced a run.

    API-ingested runs have no source folder. They group under a stable
    "api" label so the report still shows their provenance.
    """

    return source_dir.strip() or _EMPTY_FOLDER


def _summarize_runs(
    connection: sqlite3.Connection,
    rows: list[sqlite3.Row],
) -> list[dict[str, Any]]:
    summaries = [_run_row(row) for row in rows]
    for summary in summaries:
        tool_row = connection.execute(
            "SELECT COUNT(*) AS count FROM spans WHERE run_id = ? AND kind = 'tool'",
            (summary["run_id"],),
        ).fetchone()
        summary["tool_count"] = tool_row["count"]
    return summaries


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
        "source_dir": row["source_dir"],
        "metadata": json.loads(row["metadata_json"]),
        "ingested_at": row["ingested_at"],
        "label": row["label"],
        "note": row["note"],
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


def _comparison_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "comparison_id": row["comparison_id"],
        "label": row["label"],
        "run_a": row["run_a"],
        "run_b": row["run_b"],
        "created_at": row["created_at"],
        "report": json.loads(row["report_json"]),
    }


