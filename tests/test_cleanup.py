"""Deterministic tests for scheduled retention cleanup and the report retention line."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from agent_trace_workbench.cli import main
from agent_trace_workbench.main import create_app
from agent_trace_workbench.storage import TraceStore


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


def _sweep_count(store: TraceStore) -> int:
    with sqlite3.connect(store.db_path) as connection:
        return connection.execute("SELECT COUNT(*) FROM cleanup_log").fetchone()[0]


def test_sweep_runs_deletes_and_records_history(tmp_path, baseline, candidate):
    store = TraceStore(tmp_path / "sweep.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(candidate, "candidate.json")
    _backdate(store, baseline.run_id, 40)
    _backdate(store, candidate.run_id, 40)

    result = store.sweep_runs(30)

    assert result["deleted_runs"] == 2
    assert result["run_ids"] == ["run-baseline-001", "run-candidate-001"]
    assert result["sweep_id"]
    assert result["ran_at"]
    assert store.list_runs() == []
    history = store.sweep_history()
    assert len(history) == 1
    assert history[0]["sweep_id"] == result["sweep_id"]
    assert history[0]["deleted_runs"] == 2


def test_sweep_runs_records_protected_runs(tmp_path, baseline, candidate):
    store = TraceStore(tmp_path / "sweep.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(candidate, "candidate.json")
    _backdate(store, baseline.run_id, 40)
    _backdate(store, candidate.run_id, 40)
    store.update_annotations(baseline.run_id, label="golden")

    result = store.sweep_runs(30)

    assert result["protected_runs"] == 1
    assert result["deleted_runs"] == 1
    assert store.get_run(baseline.run_id) is not None
    assert store.sweep_history()[0]["protected_runs"] == 1


def test_sweep_runs_without_old_runs_records_zero_sweep(tmp_path, baseline):
    store = TraceStore(tmp_path / "sweep.db")
    store.ingest(baseline, "baseline.json")

    result = store.sweep_runs(30)

    assert result["deleted_runs"] == 0
    assert result["run_ids"] == []
    assert store.sweep_history()[0]["deleted_runs"] == 0


def test_sweep_runs_targets_explicit_run_ids(tmp_path, baseline, candidate):
    store = TraceStore(tmp_path / "sweep.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(candidate, "candidate.json")
    _backdate(store, baseline.run_id, 40)
    _backdate(store, candidate.run_id, 40)

    result = store.sweep_runs(30, run_ids=[candidate.run_id])

    assert result["deleted_runs"] == 1
    assert result["run_ids"] == ["run-candidate-001"]
    assert store.get_run(baseline.run_id) is not None


def test_sweep_runs_rejects_invalid_days(tmp_path, baseline):
    store = TraceStore(tmp_path / "sweep.db")
    store.ingest(baseline, "baseline.json")

    with pytest.raises(ValueError, match="older_than_days"):
        store.sweep_runs(0)


def test_sweep_history_orders_newest_first(tmp_path, baseline, candidate):
    store = TraceStore(tmp_path / "sweep.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(candidate, "candidate.json")
    _backdate(store, baseline.run_id, 40)
    _backdate(store, candidate.run_id, 40)

    store.sweep_runs(30)
    store.ingest(candidate, "candidate.json")
    _backdate(store, candidate.run_id, 40)
    store.sweep_runs(30)

    history = store.sweep_history()
    assert len(history) == 2
    assert history[0]["sweep_id"] != history[1]["sweep_id"]
    assert history[0]["ran_at"] >= history[1]["ran_at"]


def test_last_sweep_returns_latest_or_none(tmp_path, baseline, candidate):
    store = TraceStore(tmp_path / "sweep.db")
    store.ingest(baseline, "baseline.json")

    assert store.last_sweep() is None

    store.ingest(candidate, "candidate.json")
    _backdate(store, baseline.run_id, 40)
    _backdate(store, candidate.run_id, 40)
    store.sweep_runs(30)

    assert store.last_sweep()["deleted_runs"] == 2


def test_report_retention_line_counts_old_evidence(tmp_path, baseline, candidate):
    store = TraceStore(tmp_path / "report.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(candidate, "candidate.json")
    _backdate(store, baseline.run_id, 40)
    _backdate(store, candidate.run_id, 40)
    store.update_annotations(baseline.run_id, label="golden")

    retention = store.library_report(older_than_days=30)["retention"]

    assert retention["older_than_days"] == 30
    assert retention["eligible_runs"] == 1
    assert retention["protected_runs"] == 1
    assert retention["last_cleanup_at"] is None
    assert retention["cutoff"]


def test_report_retention_line_reflects_last_cleanup(tmp_path, baseline, candidate):
    store = TraceStore(tmp_path / "report.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(candidate, "candidate.json")
    _backdate(store, baseline.run_id, 40)
    _backdate(store, candidate.run_id, 40)
    store.sweep_runs(30)

    retention = store.library_report(older_than_days=30)["retention"]

    assert retention["eligible_runs"] == 0
    assert retention["last_cleanup_at"]


def test_report_retention_line_uses_requested_policy(tmp_path, baseline):
    store = TraceStore(tmp_path / "report.db")
    store.ingest(baseline, "baseline.json")
    _backdate(store, baseline.run_id, 40)

    strict = store.library_report(older_than_days=7)["retention"]
    lenient = store.library_report(older_than_days=60)["retention"]

    assert strict["eligible_runs"] == 1
    assert lenient["eligible_runs"] == 0


def test_cli_cleanup_runs_once_and_records_sweep(
    tmp_path, baseline, candidate, monkeypatch, capsys
):
    store = TraceStore(tmp_path / "cli.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(candidate, "candidate.json")
    _backdate(store, baseline.run_id, 40)
    _backdate(store, candidate.run_id, 40)

    monkeypatch.setattr(
        "sys.argv",
        ["atw", "--db", str(tmp_path / "cli.db"), "cleanup", "--older-than", "30"],
    )
    main()

    result = json.loads(capsys.readouterr().out)
    assert result["dry_run"] is False
    assert result["deleted_runs"] == 2
    assert result["sweep_id"]
    assert store.get_run(baseline.run_id) is None
    assert store.sweep_history(1)[0]["sweep_id"] == result["sweep_id"]


def test_cli_cleanup_dry_run_previews_without_logging(
    tmp_path, baseline, monkeypatch, capsys
):
    store = TraceStore(tmp_path / "cli.db")
    store.ingest(baseline, "baseline.json")
    _backdate(store, baseline.run_id, 40)

    monkeypatch.setattr(
        "sys.argv",
        [
            "atw",
            "--db",
            str(tmp_path / "cli.db"),
            "cleanup",
            "--older-than",
            "30",
            "--dry-run",
        ],
    )
    main()

    result = json.loads(capsys.readouterr().out)
    assert result["dry_run"] is True
    assert result["deleted_runs"] == 0
    assert result["run_ids"] == ["run-baseline-001"]
    assert "sweep_id" not in result
    assert store.get_run(baseline.run_id) is not None
    assert _sweep_count(store) == 0


def test_cli_cleanup_history_lists_sweeps(tmp_path, baseline, monkeypatch, capsys):
    store = TraceStore(tmp_path / "cli.db")
    store.ingest(baseline, "baseline.json")
    _backdate(store, baseline.run_id, 40)
    store.sweep_runs(30)

    monkeypatch.setattr(
        "sys.argv", ["atw", "--db", str(tmp_path / "cli.db"), "cleanup", "--history"]
    )
    main()

    history = json.loads(capsys.readouterr().out)
    assert len(history) == 1
    assert history[0]["deleted_runs"] == 1


def test_cli_cleanup_schedule_repeats(
    tmp_path, baseline, candidate, monkeypatch, capsys
):
    store = TraceStore(tmp_path / "cli.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(candidate, "candidate.json")
    _backdate(store, baseline.run_id, 40)
    _backdate(store, candidate.run_id, 40)

    def interrupt(_seconds: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("time.sleep", interrupt)
    monkeypatch.setattr(
        "sys.argv",
        [
            "atw",
            "--db",
            str(tmp_path / "cli.db"),
            "cleanup",
            "--older-than",
            "30",
            "--every",
            "60",
        ],
    )
    main()

    result = json.loads(capsys.readouterr().out)
    assert result["deleted_runs"] == 2
    assert store.get_run(baseline.run_id) is None


def test_cli_cleanup_rejects_zero_days(tmp_path, baseline, monkeypatch):
    store = TraceStore(tmp_path / "cli.db")
    store.ingest(baseline, "baseline.json")

    monkeypatch.setattr(
        "sys.argv",
        ["atw", "--db", str(tmp_path / "cli.db"), "cleanup", "--older-than", "0"],
    )
    with pytest.raises(SystemExit, match="--older-than"):
        main()


def test_api_cleanup_deletes_and_records_sweep(tmp_path, baseline, candidate):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())
    client.post("/api/traces", json=candidate.as_jsonable())
    store = TraceStore(tmp_path / "api.db")
    _backdate(store, baseline.run_id, 40)
    _backdate(store, candidate.run_id, 40)

    response = client.post("/api/cleanup", json={"older_than_days": 30})

    assert response.status_code == 200
    body = response.json()
    assert body["dry_run"] is False
    assert body["deleted_runs"] == 2
    assert body["sweep_id"]
    assert client.get("/api/runs/run-baseline-001").status_code == 404


def test_api_cleanup_dry_run_previews_without_recording(tmp_path, baseline):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())
    _backdate(TraceStore(tmp_path / "api.db"), baseline.run_id, 40)

    response = client.post(
        "/api/cleanup", json={"older_than_days": 30, "dry_run": True}
    )

    body = response.json()
    assert body["dry_run"] is True
    assert body["candidates"] == ["run-baseline-001"]
    assert client.get("/api/runs/run-baseline-001").status_code == 200
    assert _sweep_count(TraceStore(tmp_path / "api.db")) == 0


def test_api_cleanup_protects_labeled_runs(tmp_path, baseline, candidate):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())
    client.post("/api/traces", json=candidate.as_jsonable())
    store = TraceStore(tmp_path / "api.db")
    _backdate(store, baseline.run_id, 40)
    _backdate(store, candidate.run_id, 40)
    client.patch("/api/runs/run-baseline-001/annotations", json={"label": "golden"})

    body = client.post("/api/cleanup", json={"older_than_days": 30}).json()

    assert body["protected_runs"] == 1
    assert body["deleted_runs"] == 1
    assert client.get("/api/runs/run-baseline-001").status_code == 200


def test_api_cleanup_history_returns_sweeps(tmp_path, baseline):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())
    _backdate(TraceStore(tmp_path / "api.db"), baseline.run_id, 40)
    client.post("/api/cleanup", json={"older_than_days": 30})

    history = client.get("/api/cleanup/history").json()

    assert len(history) == 1
    assert history[0]["deleted_runs"] == 1


def test_api_cleanup_validates_payload(tmp_path, baseline):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())

    assert client.post("/api/cleanup", json={"older_than_days": 0}).status_code == 422
    assert (
        client.post(
            "/api/cleanup", json={"older_than_days": 30, "surprise": True}
        ).status_code
        == 422
    )


def test_api_report_includes_retention_line(tmp_path, baseline, candidate):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())
    client.post("/api/traces", json=candidate.as_jsonable())
    store = TraceStore(tmp_path / "api.db")
    _backdate(store, baseline.run_id, 40)
    _backdate(store, candidate.run_id, 40)

    report = client.get("/api/report", params={"older_than_days": 30}).json()

    retention = report["retention"]
    assert retention["eligible_runs"] == 2
    assert retention["protected_runs"] == 0
    assert retention["last_cleanup_at"] is None


def test_report_page_shows_retention_line(tmp_path, baseline):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())

    page = client.get("/report").text

    assert "Old evidence status" in page
    assert "Eligible for cleanup" in page


def test_cleanup_page_shows_history_section(tmp_path, baseline):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())
    _backdate(TraceStore(tmp_path / "api.db"), baseline.run_id, 40)
    client.post("/api/cleanup", json={"older_than_days": 30})

    page = client.get("/cleanup").text

    assert "Scheduled sweeps" in page
    assert "CLEANUP HISTORY" in page
