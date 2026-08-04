"""Deterministic tests for the agent comparison overlay on the failure trend."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from agent_trace_workbench.cli import main
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


def _series_bucket(series, days_ago: int) -> dict:
    day = _day(days_ago)
    return next(item for item in series if item["day"] == day)


def test_overlay_returns_two_series(tmp_path, baseline, candidate, support):
    store = TraceStore(tmp_path / "overlay.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(candidate, "candidate.json")
    store.ingest(support, "support.json")
    _set_started(store, baseline.run_id, 2)
    _set_started(store, candidate.run_id, 2)
    _set_started(store, support.run_id, 1)

    overlay = store.failure_trend_overlay(
        7, agent_name="catalog-assistant", compare_agent="support-assistant"
    )

    assert overlay["days"] == 7
    assert overlay["primary_agent"] == "catalog-assistant"
    assert overlay["compare_agent"] == "support-assistant"
    catalog = _series_bucket(overlay["primary"], 2)
    assert catalog["runs"] == 2
    assert catalog["failures"] == 1
    support_bucket = _series_bucket(overlay["compare"], 1)
    assert support_bucket["runs"] == 1
    assert support_bucket["failures"] == 0
    assert _series_bucket(overlay["compare"], 2)["runs"] == 0


def test_overlay_primary_covers_all_agents_when_unfiltered(
    tmp_path, baseline, candidate, support
):
    store = TraceStore(tmp_path / "overlay.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(candidate, "candidate.json")
    store.ingest(support, "support.json")
    _set_started(store, baseline.run_id, 2)
    _set_started(store, candidate.run_id, 2)
    _set_started(store, support.run_id, 2)

    overlay = store.failure_trend_overlay(
        7, agent_name=None, compare_agent="support-assistant"
    )

    assert overlay["primary_agent"] == ""
    assert _series_bucket(overlay["primary"], 2)["runs"] == 3


def test_overlay_keeps_empty_days_in_both_series(tmp_path, baseline, support):
    store = TraceStore(tmp_path / "overlay.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(support, "support.json")
    _set_started(store, baseline.run_id, 5)
    _set_started(store, support.run_id, 5)

    overlay = store.failure_trend_overlay(
        7, agent_name="catalog-assistant", compare_agent="support-assistant"
    )

    assert len(overlay["primary"]) == 7
    assert len(overlay["compare"]) == 7
    assert _series_bucket(overlay["primary"], 1)["runs"] == 0
    assert _series_bucket(overlay["compare"], 1)["failure_rate"] == 0.0


def test_overlay_rejects_empty_compare(tmp_path, baseline):
    store = TraceStore(tmp_path / "overlay.db")
    store.ingest(baseline, "baseline.json")

    with pytest.raises(ValueError, match="compare_agent"):
        store.failure_trend_overlay(7, agent_name=None, compare_agent="  ")


def test_overlay_rejects_compare_equal_to_primary(tmp_path, baseline):
    store = TraceStore(tmp_path / "overlay.db")
    store.ingest(baseline, "baseline.json")

    with pytest.raises(ValueError, match="must differ"):
        store.failure_trend_overlay(
            7, agent_name="catalog-assistant", compare_agent="catalog-assistant"
        )


def test_overlay_caps_large_windows(tmp_path, baseline, support):
    store = TraceStore(tmp_path / "overlay.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(support, "support.json")

    overlay = store.failure_trend_overlay(
        500, agent_name=None, compare_agent="support-assistant"
    )

    assert overlay["days"] == 90
    assert len(overlay["primary"]) == 90
    assert len(overlay["compare"]) == 90


def test_api_overlay_returns_both_series(tmp_path, baseline, candidate, support):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())
    client.post("/api/traces", json=candidate.as_jsonable())
    client.post("/api/traces", json=support.as_jsonable())
    store = TraceStore(tmp_path / "api.db")
    _set_started(store, baseline.run_id, 2)
    _set_started(store, candidate.run_id, 2)
    _set_started(store, support.run_id, 1)

    body = client.get(
        "/api/trend/overlay",
        params={
            "days": 7,
            "agent": "catalog-assistant",
            "compare": "support-assistant",
        },
    ).json()

    assert body["compare_agent"] == "support-assistant"
    assert body["primary_agent"] == "catalog-assistant"
    assert len(body["primary"]) == 7
    assert _series_bucket(body["primary"], 2)["runs"] == 2
    assert _series_bucket(body["compare"], 1)["runs"] == 1


def test_api_overlay_csv_returns_attachment(tmp_path, baseline, support):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())
    client.post("/api/traces", json=support.as_jsonable())
    store = TraceStore(tmp_path / "api.db")
    _set_started(store, baseline.run_id, 2)
    _set_started(store, support.run_id, 1)

    response = client.get(
        "/api/trend/overlay",
        params={"days": 7, "compare": "support-assistant", "format": "csv"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"] == (
        'attachment; filename="failure-trend-overlay.csv"'
    )
    lines = response.text.splitlines()
    assert lines[0] == "day,series,agent_name,runs,failures,failure_rate"
    primary_rows = [
        line for line in lines[1:] if line.startswith(f"{_day(2)},primary,")
    ]
    assert len(primary_rows) == 1
    assert primary_rows[0] == f"{_day(2)},primary,,1,0,0.0"
    assert any(
        line.startswith(f"{_day(1)},compare,support-assistant,")
        for line in lines[1:]
    )


def test_api_overlay_rejects_missing_compare(tmp_path):
    client = TestClient(create_app(tmp_path / "api.db"))

    assert (
        client.get("/api/trend/overlay", params={"days": 7}).status_code == 400
    )


def test_api_overlay_rejects_compare_equal_to_agent(tmp_path):
    client = TestClient(create_app(tmp_path / "api.db"))

    response = client.get(
        "/api/trend/overlay",
        params={"days": 7, "agent": "catalog", "compare": "catalog"},
    )

    assert response.status_code == 400
    assert "differ" in response.json()["detail"]


def test_api_overlay_rejects_unknown_format(tmp_path):
    client = TestClient(create_app(tmp_path / "api.db"))

    response = client.get(
        "/api/trend/overlay",
        params={"days": 7, "compare": "support", "format": "xml"},
    )

    assert response.status_code == 400


def test_api_overlay_does_not_shadow_day_route(tmp_path, baseline):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())
    store = TraceStore(tmp_path / "api.db")
    _set_started(store, baseline.run_id, 2)

    day_body = client.get(f"/api/trend/{_day(2)}").json()

    assert day_body["day"] == _day(2)
    assert [run["run_id"] for run in day_body["runs"]] == [baseline.run_id]


def test_cli_overlay_prints_both_series(tmp_path, baseline, support, monkeypatch, capsys):
    store = TraceStore(tmp_path / "cli.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(support, "support.json")
    _set_started(store, baseline.run_id, 2)
    _set_started(store, support.run_id, 1)

    monkeypatch.setattr(
        "sys.argv",
        [
            "atw",
            "--db",
            str(tmp_path / "cli.db"),
            "trend",
            "--days",
            "7",
            "--agent",
            "catalog-assistant",
            "--compare",
            "support-assistant",
        ],
    )
    main()

    overlay = json.loads(capsys.readouterr().out)
    assert overlay["compare_agent"] == "support-assistant"
    assert overlay["primary_agent"] == "catalog-assistant"
    assert _series_bucket(overlay["primary"], 2)["runs"] == 1
    assert _series_bucket(overlay["compare"], 1)["runs"] == 1


def test_cli_overlay_prints_csv(tmp_path, baseline, support, monkeypatch, capsys):
    store = TraceStore(tmp_path / "cli.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(support, "support.json")
    _set_started(store, baseline.run_id, 2)
    _set_started(store, support.run_id, 1)

    monkeypatch.setattr(
        "sys.argv",
        [
            "atw",
            "--db",
            str(tmp_path / "cli.db"),
            "trend",
            "--days",
            "7",
            "--compare",
            "support-assistant",
            "--format",
            "csv",
        ],
    )
    main()

    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "day,series,agent_name,runs,failures,failure_rate"
    assert lines[1].endswith(",primary,,0,0,0.0")


def test_cli_overlay_rejects_compare_equal_to_agent(
    tmp_path, baseline, monkeypatch
):
    store = TraceStore(tmp_path / "cli.db")
    store.ingest(baseline, "baseline.json")

    monkeypatch.setattr(
        "sys.argv",
        [
            "atw",
            "--db",
            str(tmp_path / "cli.db"),
            "trend",
            "--agent",
            "catalog-assistant",
            "--compare",
            "catalog-assistant",
        ],
    )
    with pytest.raises(SystemExit, match="must differ"):
        main()


def test_dashboard_shows_compare_select_for_two_agents(tmp_path, baseline, support):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())
    client.post("/api/traces", json=support.as_jsonable())

    page = client.get("/").text

    assert 'id="trend-compare"' in page
    assert 'name="compare"' in page
    assert "Compare against" in page


def test_dashboard_omits_compare_select_for_one_agent(tmp_path, baseline):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())

    page = client.get("/").text

    assert 'id="trend-compare"' not in page


def test_dashboard_draws_comparison_overlay(tmp_path, baseline, candidate, support):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())
    client.post("/api/traces", json=candidate.as_jsonable())
    client.post("/api/traces", json=support.as_jsonable())
    store = TraceStore(tmp_path / "api.db")
    _set_started(store, baseline.run_id, 2)
    _set_started(store, candidate.run_id, 2)
    _set_started(store, support.run_id, 1)

    page = client.get(
        "/",
        params={"agent": "catalog-assistant", "compare": "support-assistant"},
    ).text

    assert "trend-line-compare" in page
    assert "trend-dot-compare" in page
    assert "swatch-compare" in page
    assert "vs support-assistant" in page
    assert "failure rate per series" in page
    assert (
        "/api/trend/overlay?compare=support-assistant&amp;agent=catalog-assistant&amp;format=csv"
        in page
    )
    assert 'value="support-assistant" selected' in page


def test_dashboard_overlay_all_agents_view(tmp_path, baseline, candidate, support):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())
    client.post("/api/traces", json=candidate.as_jsonable())
    client.post("/api/traces", json=support.as_jsonable())

    page = client.get("/", params={"compare": "support-assistant"}).text

    assert "All agents" in page
    assert "trend-line-compare" in page
    assert (
        "/api/trend/overlay?compare=support-assistant&amp;format=csv" in page
    )


def test_dashboard_ignores_compare_equal_to_selected_agent(
    tmp_path, baseline, support
):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())
    client.post("/api/traces", json=support.as_jsonable())

    page = client.get(
        "/", params={"agent": "support-assistant", "compare": "support-assistant"}
    ).text

    assert "trend-line-compare" not in page
    assert "swatch-compare" not in page


def test_dashboard_empty_store_omits_overlay(tmp_path):
    client = TestClient(create_app(tmp_path / "api.db"))

    page = client.get("/", params={"compare": "support-assistant"}).text

    assert "trend-svg" not in page
    assert 'id="trend-compare"' not in page
