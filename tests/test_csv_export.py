import csv
import io
import json
import sqlite3
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from agent_trace_workbench.cli import main
from agent_trace_workbench.compare import compare_runs
from agent_trace_workbench.export import comparison_to_csv, report_to_csv, run_tools_to_csv
from agent_trace_workbench.main import create_app
from agent_trace_workbench.storage import TraceStore


def _read_csv(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text)))


def _seed(client, baseline, candidate) -> None:
    client.post("/api/traces", json=baseline.as_jsonable())
    client.post("/api/traces", json=candidate.as_jsonable())


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
