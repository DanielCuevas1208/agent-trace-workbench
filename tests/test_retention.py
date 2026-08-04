"""Deterministic tests for per-run retention and old-evidence cleanup."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from agent_trace_workbench.cli import main
from agent_trace_workbench.main import create_app
from agent_trace_workbench.storage import TraceStore

_CUTOFF = datetime.now(timezone.utc) - timedelta(days=30)


def _stamp(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def _backdate(store: TraceStore, run_id: str, days: int) -> None:
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            "UPDATE runs SET ingested_at = ? WHERE run_id = ?",
            (_stamp(days), run_id),
        )


def _span_count(store: TraceStore) -> int:
    with sqlite3.connect(store.db_path) as connection:
        return connection.execute("SELECT COUNT(*) FROM spans").fetchone()[0]


def test_candidates_exclude_fresh_runs(tmp_path, baseline):
    store = TraceStore(tmp_path / "retention.db")
    store.ingest(baseline, "baseline.json")

    assert store.retention_candidates(_CUTOFF) == []


def test_candidates_include_old_unlabeled_runs(tmp_path, baseline, candidate):
    store = TraceStore(tmp_path / "retention.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(candidate, "candidate.json")
    _backdate(store, baseline.run_id, 40)
    _backdate(store, candidate.run_id, 40)

    assert store.retention_candidates(_CUTOFF) == [
        "run-baseline-001",
        "run-candidate-001",
    ]


def test_candidates_keep_labeled_runs_by_default(tmp_path, baseline, candidate):
    store = TraceStore(tmp_path / "retention.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(candidate, "candidate.json")
    _backdate(store, baseline.run_id, 40)
    _backdate(store, candidate.run_id, 40)
    store.update_annotations(baseline.run_id, label="golden")

    assert store.retention_candidates(_CUTOFF) == ["run-candidate-001"]


def test_candidates_include_labeled_runs_when_not_kept(tmp_path, baseline):
    store = TraceStore(tmp_path / "retention.db")
    store.ingest(baseline, "baseline.json")
    _backdate(store, baseline.run_id, 40)
    store.update_annotations(baseline.run_id, label="golden")

    assert store.retention_candidates(_CUTOFF, keep_labeled=False) == [
        "run-baseline-001"
    ]


def test_candidates_respect_explicit_run_ids(tmp_path, baseline, candidate):
    store = TraceStore(tmp_path / "retention.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(candidate, "candidate.json")
    _backdate(store, baseline.run_id, 40)
    _backdate(store, candidate.run_id, 40)

    assert store.retention_candidates(_CUTOFF, run_ids=[baseline.run_id]) == [
        "run-baseline-001"
    ]


def test_candidates_ignore_missing_and_duplicate_ids(tmp_path, baseline, candidate):
    store = TraceStore(tmp_path / "retention.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(candidate, "candidate.json")
    _backdate(store, baseline.run_id, 40)
    _backdate(store, candidate.run_id, 40)

    candidates = store.retention_candidates(
        _CUTOFF,
        run_ids=[candidate.run_id, candidate.run_id, "missing-run"],
    )

    assert candidates == ["run-candidate-001"]


def test_protected_runs_lists_labeled_old_runs(tmp_path, baseline, candidate):
    store = TraceStore(tmp_path / "retention.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(candidate, "candidate.json")
    _backdate(store, baseline.run_id, 40)
    _backdate(store, candidate.run_id, 40)
    store.update_annotations(baseline.run_id, label="golden")

    assert store.protected_runs(_CUTOFF) == ["run-baseline-001"]


def test_prune_deletes_runs_and_cascades(tmp_path, baseline, candidate):
    store = TraceStore(tmp_path / "retention.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(candidate, "candidate.json")
    _backdate(store, baseline.run_id, 40)
    _backdate(store, candidate.run_id, 40)
    expected_spans = _span_count(store)
    store.save_comparison(
        baseline.run_id, candidate.run_id, "v1 vs v2", {"changed_tools": 1}
    )

    result = store.prune_runs(_CUTOFF)

    assert result["candidates"] == ["run-baseline-001", "run-candidate-001"]
    assert result["deleted_runs"] == 2
    assert result["deleted_spans"] == expected_spans
    assert result["deleted_comparisons"] == 1
    assert store.list_runs() == []
    assert store.list_comparisons() == []


def test_prune_keeps_labeled_runs(tmp_path, baseline, candidate):
    store = TraceStore(tmp_path / "retention.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(candidate, "candidate.json")
    _backdate(store, baseline.run_id, 40)
    _backdate(store, candidate.run_id, 40)
    store.update_annotations(baseline.run_id, label="golden")

    result = store.prune_runs(_CUTOFF)

    assert result["deleted_runs"] == 1
    assert result["candidates"] == ["run-candidate-001"]
    assert store.get_run(baseline.run_id) is not None
    assert store.get_run(candidate.run_id) is None


def test_prune_respects_explicit_run_ids(tmp_path, baseline, candidate):
    store = TraceStore(tmp_path / "retention.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(candidate, "candidate.json")
    _backdate(store, baseline.run_id, 40)
    _backdate(store, candidate.run_id, 40)

    result = store.prune_runs(_CUTOFF, run_ids=[candidate.run_id])

    assert result["deleted_runs"] == 1
    assert store.get_run(baseline.run_id) is not None
    assert store.get_run(candidate.run_id) is None


def test_prune_returns_zero_counts_without_matches(tmp_path, baseline):
    store = TraceStore(tmp_path / "retention.db")
    store.ingest(baseline, "baseline.json")

    result = store.prune_runs(_CUTOFF)

    assert result == {
        "candidates": [],
        "deleted_runs": 0,
        "deleted_spans": 0,
        "deleted_comparisons": 0,
    }
    assert store.get_run(baseline.run_id) is not None


def test_prune_is_idempotent(tmp_path, baseline):
    store = TraceStore(tmp_path / "retention.db")
    store.ingest(baseline, "baseline.json")
    _backdate(store, baseline.run_id, 40)

    assert store.prune_runs(_CUTOFF)["deleted_runs"] == 1
    assert store.prune_runs(_CUTOFF)["deleted_runs"] == 0


def test_runs_by_ids_keeps_input_order_and_skips_missing(tmp_path, baseline, candidate):
    store = TraceStore(tmp_path / "retention.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(candidate, "candidate.json")

    runs = store.runs_by_ids(
        [candidate.run_id, "missing-run", baseline.run_id]
    )

    assert [run["run_id"] for run in runs] == [
        "run-candidate-001",
        "run-baseline-001",
    ]
    assert runs[0]["tool_count"] == 3


def test_api_prune_dry_run_previews_without_deleting(tmp_path, baseline):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())
    _backdate(TraceStore(tmp_path / "api.db"), baseline.run_id, 40)

    response = client.post(
        "/api/prune", json={"older_than_days": 30, "dry_run": True}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["dry_run"] is True
    assert body["deleted_runs"] == 0
    assert body["candidates"] == ["run-baseline-001"]
    assert client.get("/api/runs/run-baseline-001").status_code == 200


def test_api_prune_deletes_old_runs(tmp_path, baseline, candidate):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())
    client.post("/api/traces", json=candidate.as_jsonable())
    _backdate(TraceStore(tmp_path / "api.db"), baseline.run_id, 40)

    response = client.post("/api/prune", json={"older_than_days": 30})

    assert response.status_code == 200
    body = response.json()
    assert body["deleted_runs"] == 1
    assert body["deleted_spans"] > 0
    assert client.get("/api/runs/run-baseline-001").status_code == 404
    assert client.get("/api/runs/run-candidate-001").status_code == 200


def test_api_prune_protects_labeled_runs(tmp_path, baseline, candidate):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())
    client.post("/api/traces", json=candidate.as_jsonable())
    store = TraceStore(tmp_path / "api.db")
    _backdate(store, baseline.run_id, 40)
    _backdate(store, candidate.run_id, 40)
    client.patch(
        "/api/runs/run-baseline-001/annotations", json={"label": "golden"}
    )

    body = client.post("/api/prune", json={"older_than_days": 30}).json()

    assert body["protected_runs"] == 1
    assert body["deleted_runs"] == 1
    assert body["candidates"] == ["run-candidate-001"]


def test_api_prune_targets_explicit_run_ids(tmp_path, baseline, candidate):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())
    client.post("/api/traces", json=candidate.as_jsonable())
    store = TraceStore(tmp_path / "api.db")
    _backdate(store, baseline.run_id, 40)
    _backdate(store, candidate.run_id, 40)

    body = client.post(
        "/api/prune",
        json={"older_than_days": 30, "run_ids": ["run-candidate-001"]},
    ).json()

    assert body["deleted_runs"] == 1
    assert client.get("/api/runs/run-baseline-001").status_code == 200
    assert client.get("/api/runs/run-candidate-001").status_code == 404


def test_api_prune_keeps_fresh_runs(tmp_path, baseline):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())

    body = client.post("/api/prune", json={"older_than_days": 30}).json()

    assert body["deleted_runs"] == 0
    assert body["candidates"] == []


def test_api_prune_validates_payload(tmp_path, baseline):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())

    assert (
        client.post("/api/prune", json={"older_than_days": 0}).status_code == 422
    )
    assert (
        client.post(
            "/api/prune", json={"older_than_days": 30, "run_ids": [""]}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/prune", json={"older_than_days": 30, "surprise": True}
        ).status_code
        == 422
    )


def test_cleanup_page_shows_candidates_and_form(tmp_path, baseline, candidate):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())
    client.post("/api/traces", json=candidate.as_jsonable())
    _backdate(TraceStore(tmp_path / "api.db"), baseline.run_id, 40)

    page = client.get("/cleanup").text

    assert "Retire old evidence." in page
    assert "retention-form" in page
    assert "run-baseline-001" in page
    assert "prune-form" in page


def test_cleanup_page_shows_empty_state_without_old_runs(tmp_path, baseline):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())

    page = client.get("/cleanup").text

    assert "Nothing to remove" in page
    assert "prune-form" not in page


def test_cli_prune_dry_run_prints_candidates(tmp_path, baseline, monkeypatch, capsys):
    store = TraceStore(tmp_path / "cli.db")
    store.ingest(baseline, "baseline.json")
    _backdate(store, baseline.run_id, 40)

    monkeypatch.setattr(
        "sys.argv",
        ["atw", "--db", str(tmp_path / "cli.db"), "prune", "--older-than", "30", "--dry-run"],
    )
    main()

    result = json.loads(capsys.readouterr().out)
    assert result["dry_run"] is True
    assert result["deleted_runs"] == 0
    assert result["run_ids"] == ["run-baseline-001"]
    assert store.get_run(baseline.run_id) is not None


def test_cli_prune_deletes_old_runs(tmp_path, baseline, monkeypatch, capsys):
    store = TraceStore(tmp_path / "cli.db")
    store.ingest(baseline, "baseline.json")
    _backdate(store, baseline.run_id, 40)

    monkeypatch.setattr(
        "sys.argv", ["atw", "--db", str(tmp_path / "cli.db"), "prune", "--older-than", "30"]
    )
    main()

    result = json.loads(capsys.readouterr().out)
    assert result["dry_run"] is False
    assert result["deleted_runs"] == 1
    assert result["run_ids"] == ["run-baseline-001"]
    assert store.get_run(baseline.run_id) is None


def test_cli_prune_targets_one_run(tmp_path, baseline, candidate, monkeypatch, capsys):
    store = TraceStore(tmp_path / "cli.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(candidate, "candidate.json")
    _backdate(store, baseline.run_id, 40)
    _backdate(store, candidate.run_id, 40)

    monkeypatch.setattr(
        "sys.argv",
        [
            "atw",
            "--db",
            str(tmp_path / "cli.db"),
            "prune",
            "--older-than",
            "30",
            "--run-id",
            "run-candidate-001",
        ],
    )
    main()

    assert json.loads(capsys.readouterr().out)["deleted_runs"] == 1
    assert store.get_run(baseline.run_id) is not None
    assert store.get_run(candidate.run_id) is None


def test_cli_prune_protects_labeled_runs(tmp_path, baseline, candidate, monkeypatch, capsys):
    store = TraceStore(tmp_path / "cli.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(candidate, "candidate.json")
    _backdate(store, baseline.run_id, 40)
    _backdate(store, candidate.run_id, 40)
    store.update_annotations(baseline.run_id, label="golden")

    monkeypatch.setattr(
        "sys.argv",
        ["atw", "--db", str(tmp_path / "cli.db"), "prune", "--older-than", "30"],
    )
    main()

    result = json.loads(capsys.readouterr().out)
    assert result["deleted_runs"] == 1
    assert result["protected_runs"] == 1
    assert store.get_run(baseline.run_id) is not None


def test_cli_prune_rejects_zero_days(tmp_path, baseline, monkeypatch):
    store = TraceStore(tmp_path / "cli.db")
    store.ingest(baseline, "baseline.json")

    monkeypatch.setattr(
        "sys.argv",
        ["atw", "--db", str(tmp_path / "cli.db"), "prune", "--older-than", "0"],
    )
    with pytest.raises(SystemExit, match="--older-than"):
        main()
