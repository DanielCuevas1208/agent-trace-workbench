"""Trace contracts shared by ingestion, storage, replay, and the API."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SpanKind = Literal["agent", "tool", "llm", "internal"]
SpanStatus = Literal["ok", "error", "unset"]
ToolOutcome = Literal["success", "failure", "unknown"]


class ToolCall(BaseModel):
    """A recorded tool invocation and its local outcome."""

    model_config = ConfigDict(extra="allow")

    name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: Any = None
    outcome: ToolOutcome = "unknown"
    error: str | None = None


class TraceSpan(BaseModel):
    """One span from an agent run."""

    model_config = ConfigDict(extra="allow")

    span_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    kind: SpanKind = "internal"
    parent_span_id: str | None = None
    start_time: datetime
    end_time: datetime
    status: SpanStatus = "unset"
    attributes: dict[str, Any] = Field(default_factory=dict)
    tool_call: ToolCall | None = None
    sequence: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_time_range(self) -> TraceSpan:
        if self.end_time < self.start_time:
            raise ValueError("end_time must be after start_time")
        if self.kind == "tool" and self.tool_call is None:
            raise ValueError("tool spans require tool_call data")
        return self

    @property
    def duration_ms(self) -> float:
        """Return the span duration in milliseconds."""

        return round((self.end_time - self.start_time).total_seconds() * 1000, 3)


class TraceDocument(BaseModel):
    """Portable JSON trace document accepted by the workbench."""

    model_config = ConfigDict(extra="allow")

    trace_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    agent_name: str = Field(min_length=1)
    agent_version: str = "local"
    started_at: datetime | None = None
    ended_at: datetime | None = None
    spans: list[TraceSpan] = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_document(self) -> TraceDocument:
        span_ids = [span.span_id for span in self.spans]
        if len(span_ids) != len(set(span_ids)):
            raise ValueError("span_id values must be unique within a trace")

        starts = [span.start_time for span in self.spans]
        ends = [span.end_time for span in self.spans]
        if self.started_at is None:
            self.started_at = min(starts)
        if self.ended_at is None:
            self.ended_at = max(ends)
        if self.ended_at < self.started_at:
            raise ValueError("ended_at must be after started_at")
        return self

    @property
    def duration_ms(self) -> float:
        """Return the full run duration in milliseconds."""

        if self.started_at is None or self.ended_at is None:
            return 0.0
        return round((self.ended_at - self.started_at).total_seconds() * 1000, 3)

    @property
    def status(self) -> SpanStatus:
        """Return error when any recorded span failed."""

        return "error" if any(
            span.status == "error"
            or (span.tool_call is not None and span.tool_call.outcome == "failure")
            for span in self.spans
        ) else "ok"

    def ordered_spans(self) -> list[TraceSpan]:
        """Return spans in recorded sequence order."""

        return sorted(
            self.spans,
            key=lambda span: (
                span.sequence is None,
                span.sequence if span.sequence is not None else 0,
                span.start_time,
                span.span_id,
            ),
        )

    def tool_spans(self) -> list[TraceSpan]:
        """Return only tool spans in deterministic order."""

        return [span for span in self.ordered_spans() if span.kind == "tool"]

    def as_jsonable(self) -> dict[str, Any]:
        """Return a JSON-compatible representation for storage."""

        return self.model_dump(mode="json")


def ensure_utc(value: datetime) -> datetime:
    """Normalise a timestamp for display and stable serialization."""

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class ComparisonCreate(BaseModel):
    """Request body for saving a run comparison."""

    model_config = ConfigDict(extra="forbid")

    run_a: str = Field(min_length=1)
    run_b: str = Field(min_length=1)
    label: str = Field(min_length=1, max_length=120)


class CollectorExportRequest(BaseModel):
    """Request body for sending one run to a local collector."""

    model_config = ConfigDict(extra="forbid")

    endpoint: str | None = Field(default=None, min_length=1, max_length=500)


class RunAnnotations(BaseModel):
    """Request body for updating the local label and notes on one run.

    A missing field leaves the current value untouched. An empty string
    clears it. The document requires at least one field.
    """

    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(default=None, max_length=80)
    note: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_present(self) -> RunAnnotations:
        if self.label is None and self.note is None:
            raise ValueError("Provide a label, a note, or both")
        return self
