import json
import sqlite3
import threading
import time

import pytest
from fastapi.testclient import TestClient

from agent_trace_workbench.cli import main
from agent_trace_workbench.main import create_app
from agent_trace_workbench.storage import TraceStore, _retry_on_lock


def test_store_enables_wal_and_busy_timeout(tmp_path):
    store = TraceStore(tmp_path / "trace.db")

    info = store.store_info()

    assert info["db_path"].endswith("trace.db")
    assert info["journal_mode"] == "wal"
    assert info["busy_timeout_ms"] == 5000
    assert info["synchronous"] == "normal"
    assert "sqlite_version" in info


def test_store_rejects_negative_busy_timeout(tmp_path):
    with pytest.raises(ValueError, match="busy_timeout_ms"):
        TraceStore(tmp_path / "trace.db", busy_timeout_ms=-1)


def test_two_store_instances_share_one_database(tmp_path, baseline, candidate):
    database = tmp_path / "shared.db"
    writer = TraceStore(database)
    writer.ingest(baseline, "baseline.json")

    reader = TraceStore(database)
    reader.ingest(candidate, "candidate.json")

    assert {run["run_id"] for run in writer.list_runs()} == {
        "run-baseline-001",
        "run-candidate-001",
    }
    assert reader.get_run("run-baseline-001")["agent_name"] == "catalog-assistant"


def test_reader_keeps_committed_snapshot_during_open_write(tmp_path, baseline):
    store = TraceStore(tmp_path / "trace.db")
    store.ingest(baseline, "baseline.json")

    holder = sqlite3.connect(str(store.db_path), isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    holder.execute(
        "UPDATE runs SET agent_name = ? WHERE run_id = ?",
        ("uncommitted-agent", baseline.run_id),
    )
    try:
        runs = store.list_runs()
        assert runs[0]["agent_name"] == "catalog-assistant"
    finally:
        holder.rollback()
        holder.close()

    assert store.list_runs()[0]["agent_name"] == "catalog-assistant"


def test_ingest_waits_for_a_held_write_lock(tmp_path, baseline):
    store = TraceStore(tmp_path / "trace.db", busy_timeout_ms=2000)
    holder = sqlite3.connect(str(store.db_path), isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    outcome = {}

    def ingest() -> None:
        outcome["run"] = store.ingest(baseline, "locked.json")

    thread = threading.Thread(target=ingest)
    thread.start()
    time.sleep(0.2)
    assert thread.is_alive()
    holder.rollback()
    holder.close()
    thread.join(timeout=10)

    assert not thread.is_alive()
    assert outcome["run"]["run_id"] == "run-baseline-001"
    assert store.get_run("run-baseline-001")["source_name"] == "locked.json"


def test_retry_on_lock_recovers_after_transient_locks():
    calls = {"count": 0}

    def flaky() -> str:
        calls["count"] += 1
        if calls["count"] <= 2:
            raise sqlite3.OperationalError("database is locked")
        return "done"

    assert _retry_on_lock(flaky, attempts=3) == "done"
    assert calls["count"] == 3


def test_retry_on_lock_raises_after_exhausting_attempts():
    def always_locked() -> None:
        raise sqlite3.OperationalError("database is locked")

    with pytest.raises(sqlite3.OperationalError, match="locked"):
        _retry_on_lock(always_locked, attempts=2)


def test_retry_on_lock_does_not_retry_unrelated_errors():
    def boom() -> None:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        _retry_on_lock(boom, attempts=3)


def test_cli_store_prints_configuration(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["atw", "--db", str(tmp_path / "cli.db"), "store"])
    main()

    info = json.loads(capsys.readouterr().out)
    assert info["journal_mode"] == "wal"
    assert info["busy_timeout_ms"] == 5000


def test_api_store_reports_configuration(tmp_path):
    client = TestClient(create_app(tmp_path / "api.db"))

    response = client.get("/api/store")

    assert response.status_code == 200
    body = response.json()
    assert body["journal_mode"] == "wal"
    assert body["busy_timeout_ms"] == 5000
    assert "sqlite_version" in body


def test_api_reads_busy_timeout_from_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("ATW_DB_BUSY_TIMEOUT_MS", "2500")

    client = TestClient(create_app(tmp_path / "api.db"))

    assert client.get("/api/store").json()["busy_timeout_ms"] == 2500
