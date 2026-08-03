"""Run comparison based on ordered tool calls and stable JSON values."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .models import TraceDocument, TraceSpan
from .replay import canonical_hash
from .telemetry import traced_operation


@dataclass(frozen=True)
class ToolDiff:
    """Comparison result for one tool position."""

    index: int
    tool_a: str | None
    tool_b: str | None
    state: str
    arguments_changed: bool
    outcome_changed: bool
    result_changed: bool
    duration_delta_ms: float | None
    error_a: str | None
    error_b: str | None
    argument_keys_changed: list[str] = field(default_factory=list)
    result_keys_changed: list[str] = field(default_factory=list)
    error_changed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class CompareReport:
    """Comparison summary for two trace documents."""

    run_a: dict[str, Any]
    run_b: dict[str, Any]
    tool_diffs: list[ToolDiff]

    @property
    def changed_tools(self) -> int:
        return sum(diff.state != "same" for diff in self.tool_diffs)

    @property
    def added_tools(self) -> int:
        return sum(diff.state == "added" for diff in self.tool_diffs)

    @property
    def removed_tools(self) -> int:
        return sum(diff.state == "removed" for diff in self.tool_diffs)

    @property
    def outcome_changed_tools(self) -> int:
        return sum(diff.outcome_changed for diff in self.tool_diffs)

    @property
    def error_changed_tools(self) -> int:
        return sum(diff.error_changed for diff in self.tool_diffs)

    @property
    def total_duration_delta_ms(self) -> float:
        return round(self.run_b["duration_ms"] - self.run_a["duration_ms"], 3)

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_a": self.run_a,
            "run_b": self.run_b,
            "changed_tools": self.changed_tools,
            "added_tools": self.added_tools,
            "removed_tools": self.removed_tools,
            "outcome_changed_tools": self.outcome_changed_tools,
            "error_changed_tools": self.error_changed_tools,
            "total_duration_delta_ms": self.total_duration_delta_ms,
            "tool_diffs": [diff.as_dict() for diff in self.tool_diffs],
        }


def compare_runs(run_a: TraceDocument, run_b: TraceDocument) -> CompareReport:
    """Compare tool calls by deterministic recorded position."""

    with traced_operation("compare.runs", {"run.a": run_a.run_id, "run.b": run_b.run_id}):
        tools_a = run_a.tool_spans()
        tools_b = run_b.tool_spans()
        diffs: list[ToolDiff] = []
        for index in range(max(len(tools_a), len(tools_b))):
            span_a = tools_a[index] if index < len(tools_a) else None
            span_b = tools_b[index] if index < len(tools_b) else None
            diffs.append(_diff(index + 1, span_a, span_b))
        return CompareReport(_summary(run_a), _summary(run_b), diffs)


def _diff(index: int, span_a: TraceSpan | None, span_b: TraceSpan | None) -> ToolDiff:
    if span_a is None:
        tool_b = span_b.tool_call.name if span_b and span_b.tool_call else span_b.name
        return ToolDiff(
            index,
            None,
            tool_b,
            "added",
            False,
            True,
            True,
            None,
            None,
            _error(span_b),
            error_changed=_error(span_b) is not None,
        )
    if span_b is None:
        tool_a = span_a.tool_call.name if span_a.tool_call else span_a.name
        return ToolDiff(
            index,
            tool_a,
            None,
            "removed",
            True,
            True,
            True,
            None,
            _error(span_a),
            None,
            error_changed=_error(span_a) is not None,
        )

    call_a = span_a.tool_call
    call_b = span_b.tool_call
    tool_a = call_a.name if call_a else span_a.name
    tool_b = call_b.name if call_b else span_b.name
    arguments_changed = (call_a.arguments if call_a else {}) != (call_b.arguments if call_b else {})
    outcome_a = call_a.outcome if call_a else span_a.status
    outcome_b = call_b.outcome if call_b else span_b.status
    outcome_changed = outcome_a != outcome_b
    result_changed = canonical_hash(call_a.result if call_a else None) != canonical_hash(
        call_b.result if call_b else None
    )
    names_changed = tool_a != tool_b
    state = "same"
    if names_changed or arguments_changed or outcome_changed or result_changed:
        state = "changed"
    error_a = _error(span_a)
    error_b = _error(span_b)
    return ToolDiff(
        index,
        tool_a,
        tool_b,
        state,
        arguments_changed or names_changed,
        outcome_changed,
        result_changed,
        round(span_b.duration_ms - span_a.duration_ms, 3),
        error_a,
        error_b,
        argument_keys_changed=_changed_keys(
            call_a.arguments if call_a else None, call_b.arguments if call_b else None
        ),
        result_keys_changed=_changed_keys(
            call_a.result if call_a else None, call_b.result if call_b else None
        ),
        error_changed=error_a != error_b,
    )


def _changed_keys(value_a: Any, value_b: Any) -> list[str]:
    """Return sorted top-level keys whose values differ between two mappings."""
    if not isinstance(value_a, dict) or not isinstance(value_b, dict):
        return []
    keys = set(value_a) | set(value_b)
    return sorted(key for key in keys if value_a.get(key) != value_b.get(key))


def _error(span: TraceSpan | None) -> str | None:
    return span.tool_call.error if span and span.tool_call else None


def _summary(trace: TraceDocument) -> dict[str, Any]:
    return {
        "run_id": trace.run_id,
        "trace_id": trace.trace_id,
        "agent_name": trace.agent_name,
        "agent_version": trace.agent_version,
        "status": trace.status,
        "duration_ms": trace.duration_ms,
        "tool_count": len(trace.tool_spans()),
    }


