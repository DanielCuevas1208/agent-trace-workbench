"""Deterministic tests for failure cluster grouping."""

import copy
import csv
import io
import json

import pytest
from fastapi.testclient import TestClient

from agent_trace_workbench.cli import main
from agent_trace_workbench.export import failure_clusters_summary_to_csv, failure_clusters_to_csv
from agent_trace_workbench.failure_clusters import build_failure_clusters, failure_signature
from agent_trace_workbench.main import create_app
from agent_trace_workbench.models import TraceDocument
from agent_trace_workbench.storage import TraceStore


def _read_csv(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text)))


def _clone_candidate(candidate: TraceDocument, run_id: str, error: str) -> TraceDocument:
    payload = copy.deepcopy(candidate.as_jsonable())
    payload["run_id"] = run_id
    payload["trace_id"] = f"trace-{run_id}"
    for span in payload["spans"]:
        if span["kind"] == "agent":
            span["status"] = "ok"
        if span.get("tool_call"):
            span["tool_call"]["error"] = error
    payload["status"] = "error"
    return TraceDocument.model_validate(payload)


def test_failure_signature_splits_distinct_messages():
    base = failure_signature("tool", "reserve_inventory", "reservation window expired")
    other = failure_signature("tool", "reserve_inventory", "inventory short")
    assert base != other


def test_build_failure_clusters_groups_matching_failures():
    occurrences = [
        {
            "run_id": "run-a",
            "span_id": "span-1",
            "name": "reserve_inventory",
            "kind": "tool",
            "tool_name": "reserve_inventory",
            "status": "error",
            "message": "reservation window expired",
            "agent_name": "catalog-assistant",
            "started_at": "2026-07-31T09:05:00+00:00",
        },
        {
            "run_id": "run-b",
            "span_id": "span-2",
            "name": "reserve_inventory",
            "kind": "tool",
            "tool_name": "reserve_inventory",
            "status": "error",
            "message": "reservation window expired",
            "agent_name": "catalog-assistant",
            "started_at": "2026-07-31T09:06:00+00:00",
        },
        {
            "run_id": "run-c",
            "span_id": "span-3",
            "name": "reserve_inventory",
            "kind": "tool",
            "tool_name": "reserve_inventory",
            "status": "error",
            "message": "inventory short",
            "agent_name": "catalog-assistant",
            "started_at": "2026-07-31T09:07:00+00:00",
        },
    ]
    report = build_failure_clusters(occurrences, sample_limit=5)

    assert report["cluster_count"] == 2
    assert report["occurrence_count"] == 3
    top = report["clusters"][0]
    assert top["occurrence_count"] == 2
    assert top["run_count"] == 2
    assert top["representative"]["run_id"] == "run-a"
    other = report["clusters"][1]
    assert other["occurrence_count"] == 1
    assert other["message"] == "inventory short"


def test_failure_clusters_from_store_links_spans(tmp_path, candidate):
    store = TraceStore(tmp_path / "clusters.db")
    store.ingest(
        _clone_candidate(candidate, "run-candidate-001", "reservation window expired"),
        "one.json",
    )
    store.ingest(_clone_candidate(candidate, "run-candidate-002", "reservation window expired"), "two.json")
    store.ingest(_clone_candidate(candidate, "run-candidate-003", "inventory short"), "three.json")

    report = store.failure_clusters(sample_limit=3)

    assert report["cluster_count"] == 2
    reserve = next(c for c in report["clusters"] if c["message"] == "reservation window expired")
    assert reserve["occurrence_count"] == 2
    assert {item["run_id"] for item in reserve["occurrences"]} == {
        "run-candidate-001",
        "run-candidate-002",
    }
    assert reserve["occurrences"][0]["span_href"].startswith("/runs/")


def test_failure_clusters_agent_filter(tmp_path, candidate, baseline):
    store = TraceStore(tmp_path / "clusters.db")
    store.ingest(
        _clone_candidate(candidate, "run-candidate-001", "reservation window expired"),
        "one.json",
    )
    store.ingest(baseline, "baseline.json")

    filtered = store.failure_clusters(agent_name="catalog-assistant")
    all_agents = store.failure_clusters()

    assert filtered["cluster_count"] == 1
    assert all_agents["occurrence_count"] == filtered["occurrence_count"]
    assert filtered["agent_name"] == "catalog-assistant"


def test_failure_clusters_api_and_cli(tmp_path, candidate, monkeypatch, capsys):
    store = TraceStore(tmp_path / "clusters.db")
    store.ingest(
        _clone_candidate(candidate, "run-candidate-001", "reservation window expired"),
        "one.json",
    )
    store.ingest(_clone_candidate(candidate, "run-candidate-002", "reservation window expired"), "two.json")

    app = create_app(db_path=store.db_path)
    client = TestClient(app)
    response = client.get("/api/failure-clusters")
    assert response.status_code == 200
    payload = response.json()
    assert payload["cluster_count"] == 1
    assert payload["occurrence_count"] == 2

    csv_response = client.get("/api/failure-clusters?format=csv")
    assert csv_response.status_code == 200
    rows = _read_csv(csv_response.text)
    assert len(rows) == 2
    assert rows[0]["signature"] == rows[1]["signature"]

    summary_response = client.get("/api/failure-clusters?format=csv-summary")
    assert summary_response.status_code == 200
    summary_rows = _read_csv(summary_response.text)
    assert len(summary_rows) == 1
    assert summary_rows[0]["occurrence_count"] == "2"

    page = client.get("/clusters")
    assert page.status_code == 200
    assert "reservation window expired" in page.text

    monkeypatch.setattr(
        "sys.argv",
        ["atw", "--db", str(store.db_path), "clusters"],
    )
    main()
    cli_payload = json.loads(capsys.readouterr().out)
    assert cli_payload["cluster_count"] == 1

    monkeypatch.setattr(
        "sys.argv",
        ["atw", "--db", str(store.db_path), "clusters", "--format", "csv-summary"],
    )
    main()
    csv_text = capsys.readouterr().out
    assert "representative_run_id" in csv_text.splitlines()[0]


def test_failure_clusters_csv_helpers():
    report = build_failure_clusters(
        [
            {
                "run_id": "run-a",
                "span_id": "span-a",
                "name": "tool",
                "kind": "tool",
                "tool_name": "tool",
                "status": "error",
                "message": "boom",
                "agent_name": "agent",
                "started_at": "2026-01-01T00:00:00+00:00",
            }
        ]
    )
    detail = _read_csv(failure_clusters_to_csv(report))
    summary = _read_csv(failure_clusters_summary_to_csv(report))
    assert detail[0]["message"] == "boom"
    assert summary[0]["representative_span_id"] == "span-a"