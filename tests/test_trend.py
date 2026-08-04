"""Deterministic tests for the daily failure trend on the dashboard."""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from agent_trace_workbench.main import create_app
from agent_trace_workbench.storage import TraceStore


def _day(days_ago: int) -> str:
    """Return a UTC calendar day relative to today for stable buckets."""
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime(
        "%Y-%m-%d"
    )


def _set_started(store: TraceStore, run_id: str, days_ago: int) -> None:
    day = _day(days_ago)
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            "UPDATE runs SET started_at = ?, ended_at = ? WHERE run_id = ?",
            (f"{day}T09:00:00+00:00", f"{day}T09:00:10+00:00", run_id),
        )


def _bucket(trend, days_ago: int) -> dict:
    day = _day(days_ago)
    return next(item for item in trend if item["day"] == day)


def test_failure_trend_buckets_runs_by_day(tmp_path, baseline, candidate):
    store = TraceStore(tmp_path / "trend.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(candidate, "candidate.json")
    _set_started(store, baseline.run_id, 2)
    _set_started(store, candidate.run_id, 2)

    trend = store.failure_trend(7)

    assert len(trend) == 7
    bucket = _bucket(trend, 2)
    assert bucket["runs"] == 2
    assert bucket["failures"] == 1
    assert bucket["failure_rate"] == 0.5


def test_failure_trend_keeps_empty_days_in_window(tmp_path, baseline):
    store = TraceStore(tmp_path / "trend.db")
    store.ingest(baseline, "baseline.json")
    _set_started(store, baseline.run_id, 5)

    trend = store.failure_trend(7)

    empty = _bucket(trend, 1)
    assert empty["runs"] == 0
    assert empty["failures"] == 0
    assert empty["failure_rate"] == 0.0


def test_failure_trend_spreads_across_days(tmp_path, baseline, candidate):
    store = TraceStore(tmp_path / "trend.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(candidate, "candidate.json")
    _set_started(store, baseline.run_id, 3)
    _set_started(store, candidate.run_id, 1)

    trend = store.failure_trend(7)

    assert _bucket(trend, 3)["runs"] == 1
    assert _bucket(trend, 1)["failures"] == 1
    assert _bucket(trend, 2)["runs"] == 0


def test_failure_trend_counts_each_day_once(tmp_path, baseline, candidate):
    store = TraceStore(tmp_path / "trend.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(candidate, "candidate.json")
    _set_started(store, baseline.run_id, 4)
    _set_started(store, candidate.run_id, 4)

    trend = store.failure_trend(7)

    bucket = _bucket(trend, 4)
    assert bucket["runs"] == 2
    assert bucket["failures"] == 1


def test_failure_trend_rejects_zero_days(tmp_path, baseline):
    store = TraceStore(tmp_path / "trend.db")
    store.ingest(baseline, "baseline.json")

    with pytest.raises(ValueError, match="days"):
        store.failure_trend(0)


def test_failure_trend_caps_large_windows(tmp_path, baseline):
    store = TraceStore(tmp_path / "trend.db")
    store.ingest(baseline, "baseline.json")

    trend = store.failure_trend(500)

    assert len(trend) == 90


def test_api_trend_returns_buckets(tmp_path, baseline, candidate):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())
    client.post("/api/traces", json=candidate.as_jsonable())
    store = TraceStore(tmp_path / "api.db")
    _set_started(store, baseline.run_id, 2)
    _set_started(store, candidate.run_id, 2)

    trend = client.get("/api/trend", params={"days": 7}).json()

    assert len(trend) == 7
    bucket = _bucket(trend, 2)
    assert bucket["runs"] == 2
    assert bucket["failures"] == 1


def test_api_trend_validates_days(tmp_path):
    client = TestClient(create_app(tmp_path / "api.db"))

    assert client.get("/api/trend", params={"days": 0}).status_code == 422
    assert client.get("/api/trend", params={"days": 999}).status_code == 422


def test_dashboard_shows_trend_panel(tmp_path, baseline, candidate):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())
    client.post("/api/traces", json=candidate.as_jsonable())
    store = TraceStore(tmp_path / "api.db")
    _set_started(store, baseline.run_id, 2)
    _set_started(store, candidate.run_id, 2)

    page = client.get("/").text

    assert "FAILURE TREND" in page
    assert "Do failures rise or fall?" in page
    assert "trend-svg" in page
    assert "failure runs" in page


def test_dashboard_trend_empty_state(tmp_path):
    client = TestClient(create_app(tmp_path / "api.db"))

    page = client.get("/").text

    assert "No runs in this window" in page
    assert "trend-svg" not in page
