"""Deterministic tests for the daily failure trend on the dashboard."""

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


def test_failure_trend_filters_by_agent(tmp_path, baseline, candidate, support):
    store = TraceStore(tmp_path / "trend.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(candidate, "candidate.json")
    store.ingest(support, "support.json")
    _set_started(store, baseline.run_id, 2)
    _set_started(store, candidate.run_id, 2)
    _set_started(store, support.run_id, 1)

    catalog = store.failure_trend(7, agent_name="catalog-assistant")
    support_trend = store.failure_trend(7, agent_name="support-assistant")

    bucket = _bucket(catalog, 2)
    assert bucket["runs"] == 2
    assert bucket["failures"] == 1
    assert _bucket(support_trend, 1)["runs"] == 1
    assert _bucket(support_trend, 1)["failures"] == 0
    assert _bucket(support_trend, 2)["runs"] == 0


def test_failure_trend_unknown_agent_returns_empty_buckets(tmp_path, baseline):
    store = TraceStore(tmp_path / "trend.db")
    store.ingest(baseline, "baseline.json")
    _set_started(store, baseline.run_id, 2)

    trend = store.failure_trend(7, agent_name="ghost-agent")

    assert len(trend) == 7
    assert all(bucket["runs"] == 0 for bucket in trend)


def test_trend_agents_lists_distinct_names_sorted(tmp_path, baseline, candidate, support):
    store = TraceStore(tmp_path / "trend.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(candidate, "candidate.json")
    store.ingest(support, "support.json")

    assert store.trend_agents() == ["catalog-assistant", "support-assistant"]


def test_trend_agents_returns_empty_for_empty_store(tmp_path):
    assert TraceStore(tmp_path / "trend.db").trend_agents() == []


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


def test_api_trend_filters_by_agent(tmp_path, baseline, candidate, support):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())
    client.post("/api/traces", json=candidate.as_jsonable())
    client.post("/api/traces", json=support.as_jsonable())
    store = TraceStore(tmp_path / "api.db")
    _set_started(store, baseline.run_id, 2)
    _set_started(store, candidate.run_id, 2)
    _set_started(store, support.run_id, 1)

    trend = client.get(
        "/api/trend", params={"days": 7, "agent": "support-assistant"}
    ).json()

    assert _bucket(trend, 1)["runs"] == 1
    assert _bucket(trend, 2)["runs"] == 0


def test_api_trend_csv_returns_attachment(tmp_path, baseline, candidate):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())
    client.post("/api/traces", json=candidate.as_jsonable())
    store = TraceStore(tmp_path / "api.db")
    _set_started(store, baseline.run_id, 2)
    _set_started(store, candidate.run_id, 2)

    response = client.get("/api/trend", params={"days": 7, "format": "csv"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"] == 'attachment; filename="failure-trend.csv"'
    assert response.text.splitlines()[0] == "day,agent_name,runs,failures,failure_rate"


def test_api_trend_rejects_unknown_format(tmp_path):
    client = TestClient(create_app(tmp_path / "api.db"))

    assert client.get("/api/trend", params={"format": "xml"}).status_code == 400


def test_api_trend_agents_lists_names(tmp_path, baseline, support):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())
    client.post("/api/traces", json=support.as_jsonable())

    assert client.get("/api/trend/agents").json() == [
        "catalog-assistant",
        "support-assistant",
    ]


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


def test_dashboard_shows_agent_filter_when_agents_exist(tmp_path, baseline, support):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())
    client.post("/api/traces", json=support.as_jsonable())

    page = client.get("/").text

    assert "trend-filter" in page
    assert 'name="agent"' in page
    assert "support-assistant" in page


def test_dashboard_omits_agent_filter_for_empty_store(tmp_path):
    client = TestClient(create_app(tmp_path / "api.db"))

    page = client.get("/").text

    assert "trend-filter" not in page


def test_dashboard_trend_respects_agent_filter(tmp_path, baseline, candidate, support):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())
    client.post("/api/traces", json=candidate.as_jsonable())
    client.post("/api/traces", json=support.as_jsonable())
    store = TraceStore(tmp_path / "api.db")
    _set_started(store, baseline.run_id, 2)
    _set_started(store, candidate.run_id, 2)
    _set_started(store, support.run_id, 1)

    page = client.get("/", params={"agent": "support-assistant"}).text

    assert "support-assistant" in page
    assert 'value="support-assistant" selected' in page
    assert "/api/trend?agent=support-assistant&amp;format=csv" in page
    assert "<strong>1</strong> runs recorded" in page


def test_dashboard_trend_empty_state(tmp_path):
    client = TestClient(create_app(tmp_path / "api.db"))

    page = client.get("/").text

    assert "No runs in this window" in page
    assert "trend-svg" not in page


def test_runs_on_day_lists_runs_for_one_day(tmp_path, baseline, candidate):
    store = TraceStore(tmp_path / "trend.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(candidate, "candidate.json")
    _set_started(store, baseline.run_id, 2)
    _set_started(store, candidate.run_id, 1)

    day_runs = store.runs_on_day(_day(2))

    assert [run["run_id"] for run in day_runs] == [baseline.run_id]
    assert day_runs[0]["tool_count"] == 2
    assert day_runs[0]["status"] == "ok"


def test_runs_on_day_sorts_newest_first(tmp_path, baseline, candidate):
    store = TraceStore(tmp_path / "trend.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(candidate, "candidate.json")
    _set_started(store, baseline.run_id, 2)
    _set_started(store, candidate.run_id, 2)

    day_runs = store.runs_on_day(_day(2))

    assert len(day_runs) == 2


def test_runs_on_day_filters_by_agent(tmp_path, baseline, candidate, support):
    store = TraceStore(tmp_path / "trend.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(candidate, "candidate.json")
    store.ingest(support, "support.json")
    _set_started(store, baseline.run_id, 2)
    _set_started(store, candidate.run_id, 2)
    _set_started(store, support.run_id, 2)

    day_runs = store.runs_on_day(_day(2), agent_name="catalog-assistant")

    assert {run["run_id"] for run in day_runs} == {baseline.run_id, candidate.run_id}


def test_runs_on_day_returns_empty_for_missing_day(tmp_path, baseline):
    store = TraceStore(tmp_path / "trend.db")
    store.ingest(baseline, "baseline.json")
    _set_started(store, baseline.run_id, 2)

    assert store.runs_on_day(_day(6)) == []


def test_runs_on_day_rejects_bad_format(tmp_path):
    store = TraceStore(tmp_path / "trend.db")

    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        store.runs_on_day("07/31/2026")


def test_api_trend_day_returns_runs(tmp_path, baseline, candidate):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())
    client.post("/api/traces", json=candidate.as_jsonable())
    store = TraceStore(tmp_path / "api.db")
    _set_started(store, baseline.run_id, 2)
    _set_started(store, candidate.run_id, 1)

    body = client.get(f"/api/trend/{_day(2)}").json()

    assert body["day"] == _day(2)
    assert body["agent"] == ""
    assert [run["run_id"] for run in body["runs"]] == [baseline.run_id]


def test_api_trend_day_filters_by_agent(tmp_path, baseline, candidate, support):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())
    client.post("/api/traces", json=candidate.as_jsonable())
    client.post("/api/traces", json=support.as_jsonable())
    store = TraceStore(tmp_path / "api.db")
    _set_started(store, baseline.run_id, 2)
    _set_started(store, candidate.run_id, 2)
    _set_started(store, support.run_id, 2)

    body = client.get(
        f"/api/trend/{_day(2)}", params={"agent": "support-assistant"}
    ).json()

    assert [run["run_id"] for run in body["runs"]] == [support.run_id]


def test_api_trend_day_rejects_bad_format(tmp_path):
    client = TestClient(create_app(tmp_path / "api.db"))

    assert client.get("/api/trend/not-a-day").status_code == 400


def test_api_trend_day_csv_returns_attachment(tmp_path, baseline, candidate):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())
    client.post("/api/traces", json=candidate.as_jsonable())
    store = TraceStore(tmp_path / "api.db")
    _set_started(store, baseline.run_id, 2)
    _set_started(store, candidate.run_id, 2)

    response = client.get(f"/api/trend/{_day(2)}", params={"format": "csv"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"] == (
        f'attachment; filename="runs-{_day(2)}.csv"'
    )
    assert (
        response.text.splitlines()[0]
        == "day,run_id,agent_name,status,tool_count,duration_ms,source_dir,label"
    )


def test_dashboard_shows_window_selector(tmp_path, baseline, candidate):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())
    client.post("/api/traces", json=candidate.as_jsonable())

    page = client.get("/").text

    assert "trend-days" in page
    assert 'value="14" selected' in page
    assert 'value="90"' in page


def test_dashboard_trend_respects_days_window(tmp_path, baseline, candidate):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())
    client.post("/api/traces", json=candidate.as_jsonable())
    store = TraceStore(tmp_path / "api.db")
    _set_started(store, baseline.run_id, 2)
    _set_started(store, candidate.run_id, 2)

    page = client.get("/", params={"days": 30}).text

    assert 'value="30" selected' in page
    assert "last 30 days" in page
    assert "30 days" in page


def test_dashboard_day_drill_down_lists_runs(tmp_path, baseline, candidate):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())
    client.post("/api/traces", json=candidate.as_jsonable())
    store = TraceStore(tmp_path / "api.db")
    _set_started(store, baseline.run_id, 2)
    _set_started(store, candidate.run_id, 2)

    page = client.get("/", params={"day": _day(2)}).text

    assert "DAY DRILL-DOWN" in page
    assert f"Runs on {_day(2)}" in page
    assert baseline.run_id in page
    assert f"/api/trend/{_day(2)}?" in page


def test_dashboard_day_drill_down_empty_state(tmp_path, baseline):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())
    store = TraceStore(tmp_path / "api.db")
    _set_started(store, baseline.run_id, 2)

    page = client.get("/", params={"day": _day(6)}).text

    assert "DAY DRILL-DOWN" in page
    assert f"No runs on {_day(6)}" in page


def test_dashboard_ignores_day_outside_window(tmp_path, baseline):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())
    store = TraceStore(tmp_path / "api.db")
    _set_started(store, baseline.run_id, 40)

    page = client.get("/", params={"days": 7, "day": _day(40)}).text

    assert "DAY DRILL-DOWN" not in page


def test_dashboard_drill_down_links_from_chart(tmp_path, baseline):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())
    store = TraceStore(tmp_path / "api.db")
    _set_started(store, baseline.run_id, 2)

    page = client.get("/").text

    assert f'href="/?days=14&amp;day={_day(2)}#trend-day"' in page


def test_cli_trend_day_lists_runs(tmp_path, baseline, candidate, monkeypatch, capsys):
    store = TraceStore(tmp_path / "cli.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(candidate, "candidate.json")
    _set_started(store, baseline.run_id, 2)
    _set_started(store, candidate.run_id, 1)

    monkeypatch.setattr(
        "sys.argv",
        ["atw", "--db", str(tmp_path / "cli.db"), "trend", "--day", _day(2)],
    )
    main()

    body = json.loads(capsys.readouterr().out)
    assert body["day"] == _day(2)
    assert [run["run_id"] for run in body["runs"]] == [baseline.run_id]


def test_cli_trend_day_rejects_bad_format(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        "sys.argv",
        ["atw", "--db", str(tmp_path / "cli.db"), "trend", "--day", "07/31/2026"],
    )
    with pytest.raises(SystemExit, match="YYYY-MM-DD"):
        main()


def test_status_trend_counts_runs_by_status(tmp_path, baseline, candidate):
    store = TraceStore(tmp_path / "trend.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(candidate, "candidate.json")
    _set_started(store, baseline.run_id, 2)
    _set_started(store, candidate.run_id, 2)

    breakdown = store.status_trend(7)

    assert len(breakdown) == 7
    bucket = _bucket(breakdown, 2)
    assert bucket["runs"] == 2
    assert bucket["statuses"] == {"ok": 1, "error": 1}


def test_status_trend_keeps_empty_days_in_window(tmp_path, baseline):
    store = TraceStore(tmp_path / "trend.db")
    store.ingest(baseline, "baseline.json")
    _set_started(store, baseline.run_id, 5)

    breakdown = store.status_trend(7)

    empty = _bucket(breakdown, 1)
    assert empty["runs"] == 0
    assert empty["statuses"] == {}


def test_status_trend_spreads_across_days(tmp_path, baseline, candidate):
    store = TraceStore(tmp_path / "trend.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(candidate, "candidate.json")
    _set_started(store, baseline.run_id, 3)
    _set_started(store, candidate.run_id, 1)

    breakdown = store.status_trend(7)

    assert _bucket(breakdown, 3)["statuses"] == {"ok": 1}
    assert _bucket(breakdown, 1)["statuses"] == {"error": 1}
    assert _bucket(breakdown, 2)["runs"] == 0


def test_status_trend_filters_by_agent(tmp_path, baseline, candidate, support):
    store = TraceStore(tmp_path / "trend.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(candidate, "candidate.json")
    store.ingest(support, "support.json")
    _set_started(store, baseline.run_id, 2)
    _set_started(store, candidate.run_id, 2)
    _set_started(store, support.run_id, 2)

    catalog = store.status_trend(7, agent_name="catalog-assistant")
    support_trend = store.status_trend(7, agent_name="support-assistant")

    assert _bucket(catalog, 2)["statuses"] == {"ok": 1, "error": 1}
    assert _bucket(support_trend, 2)["statuses"] == {"ok": 1}


def test_status_trend_rejects_zero_days(tmp_path, baseline):
    store = TraceStore(tmp_path / "trend.db")
    store.ingest(baseline, "baseline.json")

    with pytest.raises(ValueError, match="days"):
        store.status_trend(0)


def test_status_trend_caps_large_windows(tmp_path, baseline):
    store = TraceStore(tmp_path / "trend.db")
    store.ingest(baseline, "baseline.json")

    breakdown = store.status_trend(500)

    assert len(breakdown) == 90


def test_status_trend_unknown_agent_returns_empty_buckets(tmp_path, baseline):
    store = TraceStore(tmp_path / "trend.db")
    store.ingest(baseline, "baseline.json")
    _set_started(store, baseline.run_id, 2)

    breakdown = store.status_trend(7, agent_name="ghost-agent")

    assert len(breakdown) == 7
    assert all(bucket["statuses"] == {} for bucket in breakdown)


def test_api_status_trend_returns_buckets(tmp_path, baseline, candidate):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())
    client.post("/api/traces", json=candidate.as_jsonable())
    store = TraceStore(tmp_path / "api.db")
    _set_started(store, baseline.run_id, 2)
    _set_started(store, candidate.run_id, 2)

    buckets = client.get("/api/trend/statuses", params={"days": 7}).json()

    assert len(buckets) == 7
    assert _bucket(buckets, 2)["statuses"] == {"ok": 1, "error": 1}


def test_api_status_trend_filters_by_agent(tmp_path, baseline, candidate, support):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())
    client.post("/api/traces", json=candidate.as_jsonable())
    client.post("/api/traces", json=support.as_jsonable())
    store = TraceStore(tmp_path / "api.db")
    _set_started(store, baseline.run_id, 2)
    _set_started(store, candidate.run_id, 2)
    _set_started(store, support.run_id, 2)

    buckets = client.get(
        "/api/trend/statuses", params={"days": 7, "agent": "support-assistant"}
    ).json()

    assert _bucket(buckets, 2)["statuses"] == {"ok": 1}


def test_api_status_trend_csv_returns_attachment(tmp_path, baseline, candidate):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())
    client.post("/api/traces", json=candidate.as_jsonable())
    store = TraceStore(tmp_path / "api.db")
    _set_started(store, baseline.run_id, 2)
    _set_started(store, candidate.run_id, 2)

    response = client.get(
        "/api/trend/statuses", params={"days": 7, "format": "csv"}
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"] == (
        'attachment; filename="status-trend.csv"'
    )
    assert response.text.splitlines()[0] == "day,agent_name,status,runs"


def test_api_status_trend_rejects_unknown_format(tmp_path):
    client = TestClient(create_app(tmp_path / "api.db"))

    assert (
        client.get("/api/trend/statuses", params={"format": "xml"}).status_code == 400
    )


def test_cli_status_trend_prints_buckets(tmp_path, baseline, candidate, monkeypatch, capsys):
    store = TraceStore(tmp_path / "cli.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(candidate, "candidate.json")
    _set_started(store, baseline.run_id, 2)
    _set_started(store, candidate.run_id, 2)

    monkeypatch.setattr(
        "sys.argv",
        ["atw", "--db", str(tmp_path / "cli.db"), "trend", "--statuses", "--days", "7"],
    )
    main()

    buckets = json.loads(capsys.readouterr().out)
    assert _bucket(buckets, 2)["statuses"] == {"ok": 1, "error": 1}


def test_cli_status_trend_prints_csv(tmp_path, baseline, candidate, monkeypatch, capsys):
    store = TraceStore(tmp_path / "cli.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(candidate, "candidate.json")
    _set_started(store, baseline.run_id, 2)
    _set_started(store, candidate.run_id, 2)

    monkeypatch.setattr(
        "sys.argv",
        [
            "atw",
            "--db",
            str(tmp_path / "cli.db"),
            "trend",
            "--statuses",
            "--days",
            "7",
            "--format",
            "csv",
        ],
    )
    main()

    out = capsys.readouterr().out
    assert out.splitlines()[0] == "day,agent_name,status,runs"


def test_dashboard_shows_status_breakdown(tmp_path, baseline, candidate):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())
    client.post("/api/traces", json=candidate.as_jsonable())
    store = TraceStore(tmp_path / "api.db")
    _set_started(store, baseline.run_id, 2)
    _set_started(store, candidate.run_id, 2)

    page = client.get("/").text

    assert "STATUS BREAKDOWN" in page
    assert "What shapes each day?" in page
    assert "status-svg" in page
    assert "bar-error" in page
    assert "legend-item" in page
    assert "/api/trend/statuses?format=csv" in page


def test_dashboard_status_breakdown_respects_agent_filter(
    tmp_path, baseline, candidate, support
):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())
    client.post("/api/traces", json=candidate.as_jsonable())
    client.post("/api/traces", json=support.as_jsonable())
    store = TraceStore(tmp_path / "api.db")
    _set_started(store, baseline.run_id, 2)
    _set_started(store, candidate.run_id, 2)
    _set_started(store, support.run_id, 2)

    page = client.get("/", params={"agent": "support-assistant"}).text

    assert "STATUS BREAKDOWN" in page
    assert "/api/trend/statuses?agent=support-assistant&amp;format=csv" in page


def test_dashboard_status_breakdown_empty_state(tmp_path):
    client = TestClient(create_app(tmp_path / "api.db"))

    page = client.get("/").text

    assert "No runs in this window" in page
    assert "status-svg" not in page
