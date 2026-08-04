import csv
import io
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from agent_trace_workbench.cli import main
from agent_trace_workbench.compare import compare_runs
from agent_trace_workbench.export import (
    comparison_to_csv,
    day_runs_to_csv,
    report_to_csv,
    run_tools_to_csv,
    status_trend_to_csv,
    trend_to_csv,
)
from agent_trace_workbench.main import create_app
from agent_trace_workbench.storage import TraceStore


def _read_csv(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text)))


def _seed(client, baseline, candidate) -> None:
    client.post("/api/traces", json=baseline.as_jsonable())
    client.post("/api/traces", json=candidate.as_jsonable())


def _set_started(store: TraceStore, run_id: str, days_ago: int) -> None:
    day = (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%d")
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            "UPDATE runs SET started_at = ?, ended_at = ? WHERE run_id = ?",
            (f"{day}T09:00:00+00:00", f"{day}T09:00:10+00:00", run_id),
        )


def test_comparison_csv_has_headers_and_field_diffs(baseline, candidate):
    report = compare_runs(baseline, candidate)
    rows = _read_csv(comparison_to_csv(report))

    assert list(rows[0])[0] == "index"
    assert [row["index"] for row in rows] == ["1", "2", "3"]
    assert rows[0]["state"] == "same"
    assert rows[1]["state"] == "changed"
    assert rows[1]["result_changed"] == "yes"
    assert rows[1]["result_keys_changed"] == "available"
    assert rows[1]["arguments_changed"] == "no"
    assert rows[2]["state"] == "added"
    assert rows[2]["error_changed"] == "yes"
    assert rows[2]["error_b"] == "reservation window expired"
    assert rows[2]["duration_delta_ms"] == ""


def test_comparison_csv_escapes_commas_and_quotes(baseline):
    from agent_trace_workbench.models import TraceDocument

    payload = baseline.as_jsonable()
    payload["run_id"] = "run-escaped-001"
    payload["spans"] = [span for span in payload["spans"] if span["kind"] == "tool"]
    payload["spans"].append(
        {
            "span_id": "span-tool-003",
            "name": "reserve_inventory",
            "kind": "tool",
            "start_time": "2026-07-31T09:00:00.200000+00:00",
            "end_time": "2026-07-31T09:00:00.220000+00:00",
            "status": "error",
            "sequence": 4,
            "tool_call": {
                "name": "reserve_inventory",
                "arguments": {"sku": "lamp-01", "quantity": 10},
                "result": None,
                "outcome": "failure",
                "error": 'window expired, retry "now"',
            },
        }
    )
    run_b = TraceDocument.model_validate(payload)

    rows = _read_csv(comparison_to_csv(compare_runs(baseline, run_b)))

    added = [row for row in rows if row["state"] == "added"][0]
    assert added["error_b"] == 'window expired, retry "now"'


def test_run_tools_csv_lists_only_tool_spans(baseline, tmp_path):
    store = TraceStore(tmp_path / "api.db")
    store.ingest(baseline, "run_baseline.json")
    run = store.get_run("run-baseline-001")

    rows = _read_csv(run_tools_to_csv(run))

    assert len(rows) == 2
    assert [row["tool_name"] for row in rows] == ["search_catalog", "get_inventory"]
    assert rows[0]["arguments"] == '{"query":"desk lamp"}'
    assert rows[0]["outcome"] == "success"
    assert rows[1]["result"] == '{"available":12,"sku":"lamp-01"}'


def test_api_compare_csv_returns_attachment(tmp_path, baseline, candidate):
    client = TestClient(create_app(tmp_path / "api.db"))
    _seed(client, baseline, candidate)

    response = client.get(
        "/api/compare",
        params={"run_a": "run-baseline-001", "run_b": "run-candidate-001", "format": "csv"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "compare-" in response.headers["content-disposition"]
    assert response.headers["content-disposition"].endswith('.csv"')
    rows = _read_csv(response.text)
    assert len(rows) == 3
    assert rows[2]["tool_b"] == "reserve_inventory"


def test_api_compare_json_still_has_aggregates(tmp_path, baseline, candidate):
    client = TestClient(create_app(tmp_path / "api.db"))
    _seed(client, baseline, candidate)

    body = client.get(
        "/api/compare",
        params={"run_a": "run-baseline-001", "run_b": "run-candidate-001"},
    ).json()

    assert body["changed_tools"] == 2
    assert body["added_tools"] == 1
    assert body["removed_tools"] == 0
    assert body["outcome_changed_tools"] == 1
    assert body["error_changed_tools"] == 1
    assert body["tool_diffs"][1]["result_keys_changed"] == ["available"]


def test_api_compare_rejects_unknown_format(tmp_path, baseline, candidate):
    client = TestClient(create_app(tmp_path / "api.db"))
    _seed(client, baseline, candidate)

    response = client.get(
        "/api/compare",
        params={"run_a": "run-baseline-001", "run_b": "run-candidate-001", "format": "xml"},
    )

    assert response.status_code == 400


def test_api_export_csv_returns_run_tools(tmp_path, baseline):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())

    response = client.get("/api/runs/run-baseline-001/export", params={"format": "csv"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"] == 'attachment; filename="run-baseline-001.csv"'
    rows = _read_csv(response.text)
    assert [row["tool_name"] for row in rows] == ["search_catalog", "get_inventory"]


def test_cli_compare_csv_prints_rows(tmp_path, baseline, candidate, monkeypatch, capsys):
    store = TraceStore(tmp_path / "cli.db")
    store.ingest(baseline, "run_baseline.json")
    store.ingest(candidate, "run_candidate.json")

    monkeypatch.setattr(
        "sys.argv",
        [
            "atw",
            "--db",
            str(tmp_path / "cli.db"),
            "compare",
            "run-baseline-001",
            "run-candidate-001",
            "--format",
            "csv",
        ],
    )
    main()
    rows = _read_csv(capsys.readouterr().out)

    assert len(rows) == 3
    assert rows[1]["result_keys_changed"] == "available"


def test_cli_export_csv_writes_file(tmp_path, baseline, monkeypatch, capsys):
    store = TraceStore(tmp_path / "cli.db")
    store.ingest(baseline, "run_baseline.json")

    monkeypatch.setattr(
        "sys.argv",
        [
            "atw",
            "--db",
            str(tmp_path / "cli.db"),
            "export",
            "run-baseline-001",
            "--format",
            "csv",
            "--output",
            str(tmp_path),
        ],
    )
    main()
    report = json.loads(capsys.readouterr().out)

    assert report["exported"][0]["path"].endswith("run-baseline-001.csv")
    rows = _read_csv((tmp_path / "run-baseline-001.csv").read_text(encoding="utf-8"))
    assert [row["tool_name"] for row in rows] == ["search_catalog", "get_inventory"]


def test_report_csv_has_total_source_and_agent_rows(tmp_path, baseline, candidate):
    store = TraceStore(tmp_path / "api.db")
    store.ingest(baseline, "baseline.json", source_dir="fixtures/baseline")
    store.ingest(candidate, "candidate.json", source_dir="fixtures/candidate")
    store.update_annotations("run-baseline-001", label="golden")

    rows = _read_csv(report_to_csv(store.library_report()))

    assert list(rows[0])[0] == "section"
    total = [row for row in rows if row["section"] == "total"]
    sources = [row for row in rows if row["section"] == "source"]
    agents = [row for row in rows if row["section"] == "agent"]

    assert len(total) == 1
    assert total[0]["runs"] == "2"
    assert total[0]["labeled_runs"] == "1"
    assert total[0]["total_duration_ms"] != ""
    assert [row["source_dir"] for row in sources] == [
        "fixtures/baseline",
        "fixtures/candidate",
    ]
    assert sources[0]["runs"] == "1"
    assert agents[0]["agent_name"] == "catalog-assistant"
    assert agents[0]["avg_duration_ms"] != ""
    assert agents[0]["unlabeled_runs"] == "1"


def test_report_csv_escapes_folder_names(tmp_path, baseline):
    store = TraceStore(tmp_path / "api.db")
    store.ingest(baseline, "baseline.json", source_dir='inbox, "quoted"')

    rows = _read_csv(report_to_csv(store.library_report()))

    assert rows[1]["source_dir"] == 'inbox, "quoted"'


def test_report_csv_has_retention_row(tmp_path, baseline, candidate):
    store = TraceStore(tmp_path / "api.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(candidate, "candidate.json")
    stamp = (datetime.now(timezone.utc) - timedelta(days=40)).strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            "UPDATE runs SET ingested_at = ? WHERE run_id IN (?, ?)",
            (stamp, baseline.run_id, candidate.run_id),
        )

    rows = _read_csv(report_to_csv(store.library_report(older_than_days=30)))
    retention = [row for row in rows if row["section"] == "retention"]

    assert len(retention) == 1
    assert retention[0]["eligible_runs"] == "2"
    assert retention[0]["protected_runs"] == "0"
    assert retention[0]["cutoff"] != ""
    assert retention[0]["last_cleanup_at"] == ""


def test_trend_csv_has_headers_and_days(tmp_path, baseline, candidate):
    store = TraceStore(tmp_path / "api.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(candidate, "candidate.json")
    _set_started(store, baseline.run_id, 2)
    _set_started(store, candidate.run_id, 2)

    rows = _read_csv(trend_to_csv(store.failure_trend(7)))

    assert list(rows[0])[0] == "day"
    assert len(rows) == 7
    active = [row for row in rows if row["runs"] == "2"][0]
    assert active["failures"] == "1"
    assert active["failure_rate"] == "0.5"
    assert active["agent_name"] == ""


def test_trend_csv_repeats_the_active_agent(tmp_path, baseline, support):
    store = TraceStore(tmp_path / "api.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(support, "support.json")
    _set_started(store, baseline.run_id, 2)
    _set_started(store, support.run_id, 1)

    rows = _read_csv(
        trend_to_csv(store.failure_trend(7, agent_name="support-assistant"), "support-assistant")
    )

    assert all(row["agent_name"] == "support-assistant" for row in rows)
    assert [row for row in rows if row["runs"] == "1"][0]["agent_name"] == "support-assistant"


def test_api_trend_csv_carries_agent_filter(tmp_path, baseline, support):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())
    client.post("/api/traces", json=support.as_jsonable())
    store = TraceStore(tmp_path / "api.db")
    _set_started(store, baseline.run_id, 2)
    _set_started(store, support.run_id, 1)

    response = client.get(
        "/api/trend", params={"agent": "support-assistant", "format": "csv"}
    )

    assert response.status_code == 200
    rows = _read_csv(response.text)
    assert all(row["agent_name"] == "support-assistant" for row in rows)


def test_status_trend_csv_lists_one_row_per_status(tmp_path, baseline, candidate):
    store = TraceStore(tmp_path / "api.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(candidate, "candidate.json")
    _set_started(store, baseline.run_id, 2)
    _set_started(store, candidate.run_id, 2)

    rows = _read_csv(status_trend_to_csv(store.status_trend(7)))

    assert list(rows[0])[0] == "day"
    active = [row for row in rows if row["day"] == _day(2)]
    assert {row["status"]: row["runs"] for row in active} == {"ok": "1", "error": "1"}
    assert active[0]["agent_name"] == ""


def test_status_trend_csv_repeats_the_active_agent(tmp_path, baseline, candidate):
    store = TraceStore(tmp_path / "api.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(candidate, "candidate.json")
    _set_started(store, baseline.run_id, 2)
    _set_started(store, candidate.run_id, 2)

    rows = _read_csv(
        status_trend_to_csv(
            store.status_trend(7, agent_name="catalog-assistant"), "catalog-assistant"
        )
    )

    assert rows
    assert all(row["agent_name"] == "catalog-assistant" for row in rows)


def test_status_trend_csv_omits_empty_days(tmp_path, baseline):
    store = TraceStore(tmp_path / "api.db")
    store.ingest(baseline, "baseline.json")
    _set_started(store, baseline.run_id, 5)

    rows = _read_csv(status_trend_to_csv(store.status_trend(7)))

    assert len(rows) == 1
    assert rows[0]["status"] == "ok"
    assert rows[0]["runs"] == "1"


def test_cli_trend_prints_json_buckets(tmp_path, baseline, candidate, monkeypatch, capsys):
    store = TraceStore(tmp_path / "cli.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(candidate, "candidate.json")
    _set_started(store, baseline.run_id, 2)
    _set_started(store, candidate.run_id, 2)

    monkeypatch.setattr(
        "sys.argv",
        ["atw", "--db", str(tmp_path / "cli.db"), "trend", "--days", "7"],
    )
    main()

    trend = json.loads(capsys.readouterr().out)
    assert len(trend) == 7
    day = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%d")
    bucket = next(item for item in trend if item["day"] == day)
    assert bucket["runs"] == 2
    assert bucket["failures"] == 1


def test_cli_trend_filters_by_agent(tmp_path, baseline, support, monkeypatch, capsys):
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
            "--agent",
            "support-assistant",
        ],
    )
    main()

    trend = json.loads(capsys.readouterr().out)
    day = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    bucket = next(item for item in trend if item["day"] == day)
    assert bucket["runs"] == 1
    assert bucket["failures"] == 0


def test_cli_trend_csv_prints_rows(tmp_path, baseline, candidate, monkeypatch, capsys):
    store = TraceStore(tmp_path / "cli.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(candidate, "candidate.json")
    _set_started(store, baseline.run_id, 2)
    _set_started(store, candidate.run_id, 2)

    monkeypatch.setattr(
        "sys.argv",
        ["atw", "--db", str(tmp_path / "cli.db"), "trend", "--days", "7", "--format", "csv"],
    )
    main()

    rows = _read_csv(capsys.readouterr().out)
    assert len(rows) == 7
    assert rows[0]["agent_name"] == ""


def test_cli_trend_lists_agents(tmp_path, baseline, support, monkeypatch, capsys):
    store = TraceStore(tmp_path / "cli.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(support, "support.json")

    monkeypatch.setattr(
        "sys.argv", ["atw", "--db", str(tmp_path / "cli.db"), "trend", "--agents"]
    )
    main()

    assert json.loads(capsys.readouterr().out) == ["catalog-assistant", "support-assistant"]


def test_cli_trend_rejects_bad_days(tmp_path, baseline, monkeypatch):
    store = TraceStore(tmp_path / "cli.db")
    store.ingest(baseline, "baseline.json")

    monkeypatch.setattr(
        "sys.argv", ["atw", "--db", str(tmp_path / "cli.db"), "trend", "--days", "0"]
    )
    with pytest.raises(SystemExit, match="--days"):
        main()


def _day(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%d")


def test_day_runs_csv_renders_one_row_per_run(tmp_path, baseline, candidate):
    store = TraceStore(tmp_path / "day.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(candidate, "candidate.json")
    _set_started(store, baseline.run_id, 2)
    _set_started(store, candidate.run_id, 2)

    rows = _read_csv(day_runs_to_csv(_day(2), store.runs_on_day(_day(2))))

    assert len(rows) == 2
    assert all(row["day"] == _day(2) for row in rows)
    assert {row["run_id"] for row in rows} == {baseline.run_id, candidate.run_id}
    assert {row["status"] for row in rows} == {"ok", "error"}
    assert {row["tool_count"] for row in rows} == {"2", "3"}
    candidate_row = next(row for row in rows if row["run_id"] == candidate.run_id)
    assert candidate_row["error_count"] == "2"
    assert "reservation window expired" in candidate_row["error_summary"]


def test_day_runs_csv_carries_agent_filter(tmp_path, baseline, support):
    store = TraceStore(tmp_path / "day.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(support, "support.json")
    _set_started(store, baseline.run_id, 2)
    _set_started(store, support.run_id, 2)

    rows = _read_csv(
        day_runs_to_csv(
            _day(2),
            store.runs_on_day(_day(2), agent_name="support-assistant"),
            agent_name="support-assistant",
        )
    )

    assert [row["run_id"] for row in rows] == [support.run_id]
    assert all(row["agent_name"] == "support-assistant" for row in rows)


def test_api_trend_day_csv_carries_agent_filter(tmp_path, baseline, support):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())
    client.post("/api/traces", json=support.as_jsonable())
    store = TraceStore(tmp_path / "api.db")
    _set_started(store, baseline.run_id, 2)
    _set_started(store, support.run_id, 2)

    response = client.get(
        f"/api/trend/{_day(2)}",
        params={"agent": "support-assistant", "format": "csv"},
    )

    assert response.status_code == 200
    rows = _read_csv(response.text)
    assert [row["run_id"] for row in rows] == [support.run_id]
    assert all(row["agent_name"] == "support-assistant" for row in rows)


def test_cli_trend_day_csv_prints_rows(tmp_path, baseline, candidate, monkeypatch, capsys):
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
            "--day",
            _day(2),
            "--format",
            "csv",
        ],
    )
    main()

    rows = _read_csv(capsys.readouterr().out)
    assert len(rows) == 2
    assert rows[0]["day"] == _day(2)
