"""Deterministic tests for the server-side retention sweep scheduler."""

import sqlite3
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from agent_trace_workbench.main import create_app
from agent_trace_workbench.scheduler import CleanupScheduler
from agent_trace_workbench.storage import TraceStore


def _backdate(store: TraceStore, run_id: str, days: int) -> None:
    stamp = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            "UPDATE runs SET ingested_at = ? WHERE run_id = ?", (stamp, run_id)
        )


def test_scheduler_rejects_bad_config(tmp_path, baseline):
    store = TraceStore(tmp_path / "scheduler.db")
    store.ingest(baseline, "baseline.json")

    with pytest.raises(ValueError, match="every_seconds"):
        CleanupScheduler(store, every_seconds=0)
    with pytest.raises(ValueError, match="older_than_days"):
        CleanupScheduler(store, every_seconds=60, older_than_days=0)


def test_scheduler_sweep_records_history(tmp_path, baseline, candidate):
    store = TraceStore(tmp_path / "scheduler.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(candidate, "candidate.json")
    _backdate(store, baseline.run_id, 40)
    _backdate(store, candidate.run_id, 40)
    scheduler = CleanupScheduler(store, every_seconds=3600)

    result = scheduler.sweep()

    assert result["deleted_runs"] == 2
    assert store.get_run(baseline.run_id) is None
    assert store.sweep_history(1)[0]["sweep_id"] == result["sweep_id"]


def test_scheduler_sweep_protects_labeled_runs(tmp_path, baseline, candidate):
    store = TraceStore(tmp_path / "scheduler.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(candidate, "candidate.json")
    _backdate(store, baseline.run_id, 40)
    _backdate(store, candidate.run_id, 40)
    store.update_annotations(baseline.run_id, label="golden")
    scheduler = CleanupScheduler(store, every_seconds=3600, keep_labeled=True)

    result = scheduler.sweep()

    assert result["protected_runs"] == 1
    assert result["deleted_runs"] == 1
    assert store.get_run(baseline.run_id) is not None


def test_scheduler_start_stop_toggles_enabled(tmp_path, baseline):
    store = TraceStore(tmp_path / "scheduler.db")
    store.ingest(baseline, "baseline.json")
    scheduler = CleanupScheduler(store, every_seconds=3600)

    assert not scheduler.enabled
    scheduler.start()
    assert scheduler.enabled
    scheduler.stop()
    assert not scheduler.enabled


def test_scheduler_thread_sweeps_on_interval(tmp_path, baseline):
    store = TraceStore(tmp_path / "scheduler.db")
    store.ingest(baseline, "baseline.json")
    _backdate(store, baseline.run_id, 40)
    scheduler = CleanupScheduler(store, every_seconds=0.05)
    scheduler.start()
    try:
        deadline = time.monotonic() + 5
        while not store.sweep_history(1) and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        scheduler.stop()

    assert store.sweep_history(1)[0]["deleted_runs"] == 1
    assert store.get_run(baseline.run_id) is None


def test_scheduler_status_reflects_sweep(tmp_path, baseline):
    store = TraceStore(tmp_path / "scheduler.db")
    store.ingest(baseline, "baseline.json")
    _backdate(store, baseline.run_id, 40)
    scheduler = CleanupScheduler(store, every_seconds=3600)

    status = scheduler.status()
    assert status["enabled"] is False
    assert status["interval_seconds"] == 3600
    assert status["last_sweep_at"] is None
    assert status["last_error"] is None

    scheduler.sweep()
    status = scheduler.status()
    assert status["last_sweep_at"]
    assert status["enabled"] is False


def test_scheduler_duplicate_start_is_safe(tmp_path, baseline):
    store = TraceStore(tmp_path / "scheduler.db")
    store.ingest(baseline, "baseline.json")
    scheduler = CleanupScheduler(store, every_seconds=3600)

    scheduler.start()
    scheduler.start()
    scheduler.stop()
    assert not scheduler.enabled


def test_api_schedule_disabled_by_default(tmp_path):
    with TestClient(create_app(tmp_path / "api.db")) as client:
        status = client.get("/api/cleanup/schedule").json()

    assert status["enabled"] is False
    assert status["interval_seconds"] is None
    assert status["last_sweep_at"] is None


def test_api_schedule_enabled_via_environment(tmp_path, baseline, monkeypatch):
    monkeypatch.setenv("ATW_CLEANUP_EVERY_SECONDS", "0.05")
    monkeypatch.setenv("ATW_CLEANUP_OLDER_THAN_DAYS", "14")
    with TestClient(create_app(tmp_path / "api.db")) as client:
        client.post("/api/traces", json=baseline.as_jsonable())
        _backdate(TraceStore(tmp_path / "api.db"), baseline.run_id, 40)

        status = client.get("/api/cleanup/schedule").json()
        assert status["enabled"] is True
        assert status["older_than_days"] == 14
        assert status["keep_labeled"] is True

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if client.get("/api/runs/run-baseline-001").status_code == 404:
                break
            time.sleep(0.01)

        status = client.get("/api/cleanup/schedule").json()
        assert status["last_sweep_at"]

    assert client.get("/api/runs/run-baseline-001").status_code == 404


def test_api_schedule_keeps_labeled_runs(tmp_path, baseline, monkeypatch):
    monkeypatch.setenv("ATW_CLEANUP_EVERY_SECONDS", "0.05")
    with TestClient(create_app(tmp_path / "api.db")) as client:
        client.post("/api/traces", json=baseline.as_jsonable())
        client.patch(
            "/api/runs/run-baseline-001/annotations", json={"label": "golden"}
        )
        _backdate(TraceStore(tmp_path / "api.db"), baseline.run_id, 40)

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status = client.get("/api/cleanup/schedule").json()
            if status["last_sweep_at"]:
                break
            time.sleep(0.01)

        assert client.get("/api/runs/run-baseline-001").status_code == 200


def test_cleanup_page_shows_scheduler_status(tmp_path):
    client = TestClient(create_app(tmp_path / "api.db"))

    page = client.get("/cleanup").text

    assert "SERVER SCHEDULER" in page
    assert "Cleanup while the server runs" in page
    assert "ATW_CLEANUP_EVERY_SECONDS" in page


def test_cleanup_page_shows_active_scheduler(tmp_path, baseline, monkeypatch):
    monkeypatch.setenv("ATW_CLEANUP_EVERY_SECONDS", "0.05")
    with TestClient(create_app(tmp_path / "api.db")) as client:
        page = client.get("/cleanup").text

    assert "active" in page
