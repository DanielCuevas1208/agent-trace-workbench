"""CSV rendering for run tool calls, comparison reports, and reports.

The workbench uses the Python csv module so that every field is escaped
correctly. Arguments and results keep their structured values as compact
JSON strings inside one cell. Consumers can open the files in any
spreadsheet tool without sending trace data to a hosted service.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from .compare import CompareReport
from .telemetry import traced_operation

_YES = "yes"
_NO = "no"

_COMPARISON_HEADERS = [
    "index",
    "state",
    "tool_a",
    "tool_b",
    "arguments_changed",
    "outcome_changed",
    "result_changed",
    "error_changed",
    "duration_delta_ms",
    "error_a",
    "error_b",
    "argument_keys_changed",
    "result_keys_changed",
]

_RUN_TOOLS_HEADERS = [
    "sequence",
    "tool_name",
    "status",
    "start_time",
    "end_time",
    "duration_ms",
    "outcome",
    "error",
    "arguments",
    "result",
]

_REPORT_HEADERS = [
    "section",
    "source_dir",
    "agent_name",
    "runs",
    "ok_runs",
    "failure_runs",
    "labeled_runs",
    "unlabeled_runs",
    "tool_calls",
    "agents",
    "avg_duration_ms",
    "total_duration_ms",
    "cutoff",
    "eligible_runs",
    "protected_runs",
    "last_cleanup_at",
]

_TREND_HEADERS = ["day", "agent_name", "runs", "failures", "failure_rate"]

_SECTION_TOTAL = "total"
_SECTION_SOURCE = "source"
_SECTION_AGENT = "agent"
_SECTION_RETENTION = "retention"


def comparison_to_csv(report: CompareReport) -> str:
    """Render one comparison report as a CSV document."""

    with traced_operation("export.comparison_csv", {"diff.count": len(report.tool_diffs)}):
        rows: list[dict[str, Any]] = []
        for diff in report.tool_diffs:
            rows.append(
                {
                    "index": diff.index,
                    "state": diff.state,
                    "tool_a": diff.tool_a or "",
                    "tool_b": diff.tool_b or "",
                    "arguments_changed": _flag(diff.arguments_changed),
                    "outcome_changed": _flag(diff.outcome_changed),
                    "result_changed": _flag(diff.result_changed),
                    "error_changed": _flag(diff.error_changed),
                    "duration_delta_ms": _number(diff.duration_delta_ms),
                    "error_a": diff.error_a or "",
                    "error_b": diff.error_b or "",
                    "argument_keys_changed": ", ".join(diff.argument_keys_changed),
                    "result_keys_changed": ", ".join(diff.result_keys_changed),
                }
            )
        return _to_csv(_COMPARISON_HEADERS, rows)


def run_tools_to_csv(run: dict[str, Any]) -> str:
    """Render the recorded tool calls of one run as a CSV document."""

    spans = run.get("spans", [])
    attributes = {"run.id": run.get("run_id", ""), "span.count": len(spans)}
    with traced_operation("export.run_csv", attributes):
        rows: list[dict[str, Any]] = []
        for span in spans:
            if span.get("kind") != "tool":
                continue
            call = span.get("tool_call") or {}
            rows.append(
                {
                    "sequence": span.get("sequence", ""),
                    "tool_name": call.get("name") or span.get("name", ""),
                    "status": span.get("status", ""),
                    "start_time": span.get("start_time", ""),
                    "end_time": span.get("end_time", ""),
                    "duration_ms": _number(span.get("duration_ms")),
                    "outcome": call.get("outcome") or "",
                    "error": call.get("error") or "",
                    "arguments": _json_cell(call.get("arguments")),
                    "result": _json_cell(call.get("result")),
                }
            )
        return _to_csv(_RUN_TOOLS_HEADERS, rows)


def report_to_csv(report: dict[str, Any]) -> str:
    """Render the library report as one CSV document.

    The document keeps every section in one file. A section column marks
    each row as the library total, one source folder, one agent, or the
    retention line. Leave a cell empty when the section does not carry
    that metric.
    """

    totals = report["totals"]
    retention = report.get("retention", {})
    with traced_operation(
        "export.report_csv", {"report.runs": totals["runs"], "report.agents": totals["agents"]}
    ):
        rows: list[dict[str, Any]] = [
            {
                "section": _SECTION_TOTAL,
                "source_dir": "",
                "agent_name": "",
                "runs": totals["runs"],
                "ok_runs": totals["ok_runs"],
                "failure_runs": totals["failure_runs"],
                "labeled_runs": totals["labeled_runs"],
                "unlabeled_runs": totals["unlabeled_runs"],
                "tool_calls": totals["tool_calls"],
                "agents": totals["agents"],
                "avg_duration_ms": "",
                "total_duration_ms": totals["total_duration_ms"],
                "cutoff": "",
                "eligible_runs": "",
                "protected_runs": "",
                "last_cleanup_at": "",
            }
        ]
        for item in report.get("by_source", []):
            rows.append(
                {
                    "section": _SECTION_SOURCE,
                    "source_dir": item["source_dir"],
                    "agent_name": "",
                    "runs": item["runs"],
                    "ok_runs": "",
                    "failure_runs": item["failure_runs"],
                    "labeled_runs": "",
                    "unlabeled_runs": item["unlabeled_runs"],
                    "tool_calls": item["tool_calls"],
                    "agents": item["agents"],
                    "avg_duration_ms": "",
                    "total_duration_ms": "",
                    "cutoff": "",
                    "eligible_runs": "",
                    "protected_runs": "",
                    "last_cleanup_at": "",
                }
            )
        for item in report.get("by_agent", []):
            rows.append(
                {
                    "section": _SECTION_AGENT,
                    "source_dir": "",
                    "agent_name": item["agent_name"],
                    "runs": item["runs"],
                    "ok_runs": "",
                    "failure_runs": item["failure_runs"],
                    "labeled_runs": "",
                    "unlabeled_runs": item["unlabeled_runs"],
                    "tool_calls": item["tool_calls"],
                    "agents": "",
                    "avg_duration_ms": item["avg_duration_ms"],
                    "total_duration_ms": "",
                    "cutoff": "",
                    "eligible_runs": "",
                    "protected_runs": "",
                    "last_cleanup_at": "",
                }
            )
        rows.append(
            {
                "section": _SECTION_RETENTION,
                "source_dir": "",
                "agent_name": "",
                "runs": "",
                "ok_runs": "",
                "failure_runs": "",
                "labeled_runs": "",
                "unlabeled_runs": "",
                "tool_calls": "",
                "agents": "",
                "avg_duration_ms": "",
                "total_duration_ms": "",
                "cutoff": retention.get("cutoff", ""),
                "eligible_runs": retention.get("eligible_runs", ""),
                "protected_runs": retention.get("protected_runs", ""),
                "last_cleanup_at": retention.get("last_cleanup_at") or "",
            }
        )
        return _to_csv(_REPORT_HEADERS, rows)


def trend_to_csv(trend: list[dict[str, Any]], agent_name: str = "") -> str:
    """Render a daily failure trend as a CSV document.

    The document lists one row per day. The agent_name cell repeats the
    active filter, so a filtered file stays self-describing. Leave the
    cell empty for the all-agents view.
    """

    with traced_operation(
        "export.trend_csv", {"trend.days": len(trend), "trend.agent": agent_name}
    ):
        rows: list[dict[str, Any]] = []
        for bucket in trend:
            rows.append(
                {
                    "day": bucket["day"],
                    "agent_name": agent_name,
                    "runs": bucket["runs"],
                    "failures": bucket["failures"],
                    "failure_rate": _number(bucket.get("failure_rate")),
                }
            )
        return _to_csv(_TREND_HEADERS, rows)


def _to_csv(headers: list[str], rows: list[dict[str, Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=headers, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def _flag(value: bool) -> str:
    return _YES if value else _NO


def _number(value: Any) -> str:
    return "" if value is None else str(value)


def _json_cell(value: Any) -> str:
    if value is None:
        return ""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
