"""Deterministic tests for the run-level error timeline."""

import csv
import io
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from agent_trace_workbench.cli import main
from agent_trace_workbench.export import error_timeline_to_csv
from agent_trace_workbench.main import create_app
from agent_trace_workbench.models import TraceDocument
from agent_trace_workbench.storage import TraceStore


def _read_csv(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text)))


def test_error_timeline_lists_failed_spans_in_order(tmp_path, candidate):
    store = TraceStore(tmp_path / "timeline.db")
    store.ingest(candidate, "candidate.json")

    timeline = store.error_timeline(candidate.run_id)

    assert timeline["run_id"] == "run-candidate-001"
    assert timeline["duration_ms"] == 280.0
    assert timeline["error_count"] == 2
    assert [event["span_id"] for event in timeline["events"]] == [
        "span-agent-101",
        "span-tool-103",
    ]
    agent_event = timeline["events"][0]
    assert agent_event["start_offset_ms"] == 0.0
    assert agent_event["end_offset_ms"] == 280.0
    assert agent_event["kind"] == "agent"
    assert agent_event["error"] == "agent.run ended with status error"
    tool_event = timeline["events"][1]
    assert tool_event["start_offset_ms"] == 205.0
    assert tool_event["end_offset_ms"] == 260.0
    assert tool_event["kind"] == "tool"
    assert tool_event["error"] == "reservation window expired"


def test_error_timeline_returns_empty_for_clean_run(tmp_path, baseline):
    store = TraceStore(tmp_path / "timeline.db")
    store.ingest(baseline, "baseline.json")

    timeline = store.error_timeline(baseline.run_id)

    assert timeline["error_count"] == 0
    assert timeline["events"] == []


def test_error_timeline_returns_none_for_missing_run(tmp_path):
    store = TraceStore(tmp_path / "timeline.db")

    assert store.error_timeline("not-here") is None


def test_error_timeline_uses_fallback_for_failure_without_error(tmp_path, baseline):
    payload = baseline.as_jsonable()
    payload["run_id"] = "run-fallback-001"
    payload["ended_at"] = "2026-07-31T09:00:00.220000+00:00"
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
    store = TraceStore(tmp_path / "timeline.db")
    store.ingest(TraceDocument.model_validate(payload), "fallback.json")

    timeline = store.error_timeline("run-fallback-001")

    assert timeline["error_count"] == 1
    assert timeline["events"][0]["error"] == "reserve_inventory reported a failure outcome"


def test_error_timeline_clamps_negative_offsets(tmp_path, candidate):
    store = TraceStore(tmp_path / "timeline.db")
    store.ingest(candidate, "candidate.json")
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            "UPDATE runs SET started_at = ? WHERE run_id = ?",
            ("2026-07-31T09:05:00.300000+00:00", candidate.run_id),
        )

    timeline = store.error_timeline(candidate.run_id)

    assert timeline["error_count"] == 2
    assert all(event["start_offset_ms"] >= 0 for event in timeline["events"])
    assert all(event["end_offset_ms"] >= 0 for event in timeline["events"])


def test_error_timeline_csv_has_headers_and_rows(tmp_path, candidate):
    store = TraceStore(tmp_path / "timeline.db")
    store.ingest(candidate, "candidate.json")

    rows = _read_csv(error_timeline_to_csv(store.error_timeline(candidate.run_id)))

    assert list(rows[0])[0] == "run_id"
    assert len(rows) == 2
    assert rows[0]["name"] == "agent.run"
    assert rows[0]["start_offset_ms"] == "0.0"
    assert rows[1]["error"] == "reservation window expired"
    assert rows[1]["run_id"] == "run-candidate-001"


def test_api_timeline_returns_events(tmp_path, candidate):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=candidate.as_jsonable())

    body = client.get("/api/runs/run-candidate-001/timeline").json()

    assert body["error_count"] == 2
    assert body["duration_ms"] == 280.0
    assert body["events"][-1]["error"] == "reservation window expired"


def test_api_timeline_csv_returns_attachment(tmp_path, candidate):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=candidate.as_jsonable())

    response = client.get(
        "/api/runs/run-candidate-001/timeline", params={"format": "csv"}
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"] == (
        'attachment; filename="run-candidate-001-error-timeline.csv"'
    )
    assert (
        response.text.splitlines()[0]
        == "run_id,span_id,sequence,name,kind,status,start_offset_ms,"
        "end_offset_ms,duration_ms,error"
    )


def test_api_timeline_missing_run_returns_404(tmp_path):
    client = TestClient(create_app(tmp_path / "api.db"))

    assert client.get("/api/runs/not-here/timeline").status_code == 404


def test_api_timeline_rejects_unknown_format(tmp_path, candidate):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=candidate.as_jsonable())

    response = client.get(
        "/api/runs/run-candidate-001/timeline", params={"format": "xml"}
    )

    assert response.status_code == 400


def test_cli_timeline_prints_json(tmp_path, candidate, monkeypatch, capsys):
    store = TraceStore(tmp_path / "cli.db")
    store.ingest(candidate, "candidate.json")

    monkeypatch.setattr(
        "sys.argv",
        ["atw", "--db", str(tmp_path / "cli.db"), "timeline", "run-candidate-001"],
    )
    main()

    timeline = json.loads(capsys.readouterr().out)
    assert timeline["error_count"] == 2
    assert timeline["events"][-1]["error"] == "reservation window expired"


def test_cli_timeline_prints_csv(tmp_path, candidate, monkeypatch, capsys):
    store = TraceStore(tmp_path / "cli.db")
    store.ingest(candidate, "candidate.json")

    monkeypatch.setattr(
        "sys.argv",
        [
            "atw",
            "--db",
            str(tmp_path / "cli.db"),
            "timeline",
            "run-candidate-001",
            "--format",
            "csv",
        ],
    )
    main()

    out = capsys.readouterr().out
    assert (
        out.splitlines()[0]
        == "run_id,span_id,sequence,name,kind,status,start_offset_ms,"
        "end_offset_ms,duration_ms,error"
    )
    assert out.splitlines()[1] == (
        "run-candidate-001,span-agent-101,0,agent.run,agent,error,0.0,280.0,280.0,"
        "agent.run ended with status error"
    )


def test_cli_timeline_missing_run_exits(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "sys.argv", ["atw", "--db", str(tmp_path / "cli.db"), "timeline", "not-here"]
    )

    with pytest.raises(SystemExit, match="Run not found"):
        main()


def test_run_page_shows_error_timeline(tmp_path, candidate):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=candidate.as_jsonable())

    page = client.get("/runs/run-candidate-001").text

    assert "ERROR TIMELINE" in page
    assert "When did this run fail?" in page
    assert "timeline-svg" in page
    assert "2 failed spans" in page
    assert "reservation window expired" in page
    assert "/api/runs/run-candidate-001/timeline" in page
    assert "#span-span-tool-103" in page


def test_run_page_timeline_empty_state(tmp_path, baseline):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())

    page = client.get("/runs/run-baseline-001").text

    assert "ERROR TIMELINE" in page
    assert "No failed spans" in page
    assert "timeline-svg" not in page
