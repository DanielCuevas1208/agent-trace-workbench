"""Deterministic tests for the run-level span detail panel."""

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from agent_trace_workbench.cli import main
from agent_trace_workbench.main import create_app
from agent_trace_workbench.models import TraceDocument
from agent_trace_workbench.storage import TraceStore


def test_span_detail_returns_full_tool_record(tmp_path, candidate):
    store = TraceStore(tmp_path / "detail.db")
    store.ingest(candidate, "candidate.json")

    detail = store.span_detail(candidate.run_id, "span-tool-103")

    assert detail["run_id"] == "run-candidate-001"
    assert detail["span_id"] == "span-tool-103"
    assert detail["name"] == "reserve_inventory"
    assert detail["kind"] == "tool"
    assert detail["status"] == "error"
    assert detail["sequence"] == 3
    assert detail["parent_span_id"] == "span-agent-101"
    assert detail["start_offset_ms"] == 205.0
    assert detail["end_offset_ms"] == 260.0
    assert detail["duration_ms"] == 55.0
    assert detail["error"] == "reservation window expired"
    assert detail["tool_call"]["arguments"] == {"sku": "lamp-01", "quantity": 10}
    assert detail["tool_call"]["result"] is None
    assert detail["tool_call"]["outcome"] == "failure"
    assert detail["attributes"] == {"tool.version": "fixture-2"}


def test_span_detail_reports_agent_span_with_generated_message(tmp_path, candidate):
    store = TraceStore(tmp_path / "detail.db")
    store.ingest(candidate, "candidate.json")

    detail = store.span_detail(candidate.run_id, "span-agent-101")

    assert detail["name"] == "agent.run"
    assert detail["kind"] == "agent"
    assert detail["start_offset_ms"] == 0.0
    assert detail["end_offset_ms"] == 280.0
    assert detail["error"] == "agent.run ended with status error"
    assert detail["tool_call"] is None


def test_span_detail_clamps_negative_offsets(tmp_path, candidate):
    store = TraceStore(tmp_path / "detail.db")
    store.ingest(candidate, "candidate.json")
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            "UPDATE runs SET started_at = ? WHERE run_id = ?",
            ("2026-07-31T09:05:00.300000+00:00", candidate.run_id),
        )

    detail = store.span_detail(candidate.run_id, "span-tool-101")

    assert detail["start_offset_ms"] >= 0
    assert detail["end_offset_ms"] >= 0


def test_span_detail_returns_none_for_missing_run(tmp_path, candidate):
    store = TraceStore(tmp_path / "detail.db")
    store.ingest(candidate, "candidate.json")

    assert store.span_detail("not-here", "span-tool-103") is None


def test_span_detail_returns_none_for_missing_span(tmp_path, candidate):
    store = TraceStore(tmp_path / "detail.db")
    store.ingest(candidate, "candidate.json")

    assert store.span_detail(candidate.run_id, "span-missing") is None


def test_span_detail_preserves_failure_without_error(tmp_path, baseline):
    payload = baseline.as_jsonable()
    payload["run_id"] = "run-fallback-001"
    tool_spans = [span for span in payload["spans"] if span["kind"] == "tool"]
    tool_spans.append(
        {
            "span_id": "span-tool-003",
            "name": "reserve_inventory",
            "kind": "tool",
            "start_time": "2026-07-31T09:00:00.170000+00:00",
            "end_time": "2026-07-31T09:00:00.200000+00:00",
            "status": "error",
            "sequence": 4,
            "tool_call": {
                "name": "reserve_inventory",
                "arguments": {"sku": "lamp-01", "quantity": 10},
                "result": None,
                "outcome": "failure",
                "error": None,
            },
        }
    )
    payload["spans"] = tool_spans
    store = TraceStore(tmp_path / "detail.db")
    store.ingest(TraceDocument.model_validate(payload), "fallback.json")

    detail = store.span_detail("run-fallback-001", "span-tool-003")

    assert detail["error"] == "reserve_inventory reported a failure outcome"


def test_api_span_detail_returns_record(tmp_path, candidate):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=candidate.as_jsonable())

    response = client.get("/api/runs/run-candidate-001/spans/span-tool-103")

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "reserve_inventory"
    assert body["error"] == "reservation window expired"
    assert body["start_offset_ms"] == 205.0


def test_api_span_detail_missing_span_returns_404(tmp_path, candidate):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=candidate.as_jsonable())

    assert client.get("/api/runs/run-candidate-001/spans/span-missing").status_code == 404


def test_api_span_detail_missing_run_returns_404(tmp_path):
    client = TestClient(create_app(tmp_path / "api.db"))

    assert client.get("/api/runs/not-here/spans/span-tool-103").status_code == 404


def test_cli_span_prints_json(tmp_path, candidate, monkeypatch, capsys):
    store = TraceStore(tmp_path / "cli.db")
    store.ingest(candidate, "candidate.json")

    monkeypatch.setattr(
        "sys.argv",
        [
            "atw",
            "--db",
            str(tmp_path / "cli.db"),
            "span",
            "run-candidate-001",
            "span-tool-103",
        ],
    )
    main()

    detail = json.loads(capsys.readouterr().out)
    assert detail["name"] == "reserve_inventory"
    assert detail["error"] == "reservation window expired"


def test_cli_span_missing_span_exits(tmp_path, candidate, monkeypatch):
    store = TraceStore(tmp_path / "cli.db")
    store.ingest(candidate, "candidate.json")
    monkeypatch.setattr(
        "sys.argv",
        [
            "atw",
            "--db",
            str(tmp_path / "cli.db"),
            "span",
            "run-candidate-001",
            "span-missing",
        ],
    )

    with pytest.raises(SystemExit, match="Span not found"):
        main()


def test_run_page_shows_span_detail_panel(tmp_path, candidate):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=candidate.as_jsonable())

    page = client.get("/runs/run-candidate-001").text

    assert "span-detail-panel" in page
    assert "data-run-id=\"run-candidate-001\"" in page
    assert "data-span-id=\"span-tool-103\"" in page
    assert "Inspect" in page
    assert "#span-span-tool-103" in page


def test_run_page_panel_hidden_for_clean_run(tmp_path, baseline):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())

    page = client.get("/runs/run-baseline-001").text

    assert "span-detail-panel" not in page
    assert "timeline-svg" not in page
