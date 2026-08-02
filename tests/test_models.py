import pytest
from pydantic import ValidationError

from agent_trace_workbench.models import TraceDocument


def test_trace_derives_duration_and_orders_tool_spans(baseline):
    assert baseline.duration_ms == 220.0
    assert [span.tool_call.name for span in baseline.tool_spans()] == [
        "search_catalog",
        "get_inventory",
    ]
    assert baseline.status == "ok"


def test_tool_failure_sets_run_error(baseline):
    payload = baseline.as_jsonable()
    payload["spans"][1]["status"] = "ok"
    payload["spans"][1]["tool_call"]["outcome"] = "failure"
    assert TraceDocument.model_validate(payload).status == "error"


def test_trace_rejects_duplicate_span_ids(baseline):
    payload = baseline.as_jsonable()
    payload["spans"].append(payload["spans"][0].copy())
    with pytest.raises(ValidationError, match="unique"):
        TraceDocument.model_validate(payload)


def test_trace_rejects_tool_without_call(baseline):
    payload = baseline.as_jsonable()
    payload["spans"][1].pop("tool_call")
    with pytest.raises(ValidationError, match="tool_call"):
        TraceDocument.model_validate(payload)