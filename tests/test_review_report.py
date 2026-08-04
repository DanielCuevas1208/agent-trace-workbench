import csv
import io
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from agent_trace_workbench.cli import main
from agent_trace_workbench.ingestion import DirectoryWatcher
from agent_trace_workbench.main import create_app
from agent_trace_workbench.storage import TraceStore

_LEGACY_SCHEMA = """
CREATE TABLE runs (
    run_id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    agent_version TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    duration_ms REAL NOT NULL,
    source_name TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    ingested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    label TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT ''
);
"""


def test_store_unreviewed_runs_excludes_labeled(tmp_path, baseline, candidate):
    store = TraceStore(tmp_path / "review.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(candidate, "candidate.json")
    store.update_annotations("run-baseline-001", label="golden")

    reviewed = store.unreviewed_runs()

    assert [run["run_id"] for run in reviewed] == ["run-candidate-001"]
    assert reviewed[0]["tool_count"] == 3


def test_store_unreviewed_runs_returns_empty_when_all_labeled(tmp_path, baseline):
    store = TraceStore(tmp_path / "review.db")
    store.ingest(baseline, "baseline.json")
    store.update_annotations("run-baseline-001", label="done")

    assert store.unreviewed_runs() == []


def test_store_report_totals(tmp_path, baseline, candidate):
    store = TraceStore(tmp_path / "report.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(candidate, "candidate.json")
    store.update_annotations("run-baseline-001", label="golden")

    totals = store.library_report()["totals"]

    assert totals["runs"] == 2
    assert totals["ok_runs"] == 1
    assert totals["failure_runs"] == 1
    assert totals["labeled_runs"] == 1
    assert totals["unlabeled_runs"] == 1
    assert totals["tool_calls"] == 5
    assert totals["agents"] == 1
    assert totals["total_duration_ms"] > 0


def test_store_report_groups_by_agent(tmp_path, baseline, candidate):
    store = TraceStore(tmp_path / "report.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(candidate, "candidate.json")

    rows = store.library_report()["by_agent"]

    assert len(rows) == 1
    row = rows[0]
    assert row["agent_name"] == "catalog-assistant"
    assert row["runs"] == 2
    assert row["tool_calls"] == 5
    assert row["failure_runs"] == 1
    assert row["unlabeled_runs"] == 2
    assert row["avg_duration_ms"] > 0


def test_store_report_groups_by_source_folder(tmp_path, baseline, candidate):
    store = TraceStore(tmp_path / "report.db")
    store.ingest(baseline, "baseline.json", source_dir="fixtures/baseline")
    store.ingest(candidate, "candidate.json", source_dir="fixtures/candidate")

    report = store.library_report()

    assert report["totals"]["sources"] == 2
    assert [row["source_dir"] for row in report["by_source"]] == [
        "fixtures/baseline",
        "fixtures/candidate",
    ]
    assert report["by_source"][0]["runs"] == 1
    assert report["by_source"][0]["agents"] == 1


def test_store_report_groups_api_ingested_runs_under_api_folder(tmp_path, baseline):
    store = TraceStore(tmp_path / "report.db")
    store.ingest(baseline, "baseline.json")

    rows = store.library_report()["by_source"]

    assert [row["source_dir"] for row in rows] == ["api"]
    assert store.get_run("run-baseline-001")["source_dir"] == ""


def test_store_migrates_legacy_database_without_source_dir(tmp_path, baseline):
    database = tmp_path / "legacy.db"
    legacy = sqlite3.connect(str(database))
    legacy.executescript(_LEGACY_SCHEMA)
    legacy.commit()
    legacy.close()

    store = TraceStore(database)
    store.ingest(baseline, "baseline.json")

    columns = {
        str(row[1]) for row in sqlite3.connect(str(database)).execute("PRAGMA table_info(runs)")
    }
    assert "source_dir" in columns
    assert store.get_run("run-baseline-001")["source_dir"] == ""


def test_watcher_records_source_directory(tmp_path, baseline):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "trace.json").write_text(json.dumps(baseline.as_jsonable()), encoding="utf-8")

    DirectoryWatcher(TraceStore(tmp_path / "traces.db"), inbox).scan()

    store = TraceStore(tmp_path / "traces.db")
    assert store.get_run("run-baseline-001")["source_dir"] == str(inbox)


def test_api_lists_review_runs(tmp_path, baseline, candidate):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())
    client.post("/api/traces", json=candidate.as_jsonable())

    body = client.get("/api/review").json()

    assert {run["run_id"] for run in body} == {
        "run-baseline-001",
        "run-candidate-001",
    }


def test_api_returns_library_report(tmp_path, baseline, candidate):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())
    client.post("/api/traces", json=candidate.as_jsonable())

    report = client.get("/api/report").json()

    assert report["totals"]["runs"] == 2
    assert report["totals"]["tool_calls"] == 5
    assert report["by_agent"][0]["agent_name"] == "catalog-assistant"


def test_review_page_shows_unlabeled_runs(tmp_path, baseline, candidate):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())
    client.post("/api/traces", json=candidate.as_jsonable())
    client.patch("/api/runs/run-baseline-001/annotations", json={"label": "golden"})

    page = client.get("/review").text

    assert "Unlabeled runs" in page
    assert "run-candidate-001" in page
    assert "run-baseline-001" not in page


def test_report_page_shows_totals_and_folder_rows(tmp_path, baseline, candidate):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())
    client.post("/api/traces", json=candidate.as_jsonable())

    page = client.get("/report").text

    assert "Folder-level summary" in page or "Library report" in page
    assert "Evidence per folder" in page
    assert "Evidence per agent" in page
    assert "catalog-assistant" in page


def test_cli_review_lists_unlabeled_runs(tmp_path, baseline, candidate, monkeypatch, capsys):
    store = TraceStore(tmp_path / "cli.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(candidate, "candidate.json")
    store.update_annotations("run-baseline-001", label="golden")

    monkeypatch.setattr(
        "sys.argv", ["atw", "--db", str(tmp_path / "cli.db"), "review"]
    )
    main()

    results = json.loads(capsys.readouterr().out)
    assert [run["run_id"] for run in results] == ["run-candidate-001"]


def test_cli_report_prints_library_summary(tmp_path, baseline, candidate, monkeypatch, capsys):
    store = TraceStore(tmp_path / "cli.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(candidate, "candidate.json")

    monkeypatch.setattr(
        "sys.argv", ["atw", "--db", str(tmp_path / "cli.db"), "report"]
    )
    main()

    report = json.loads(capsys.readouterr().out)
    assert report["totals"]["runs"] == 2
    assert report["totals"]["failure_runs"] == 1


def test_store_bulk_labels_selected_runs(tmp_path, baseline, candidate):
    store = TraceStore(tmp_path / "bulk.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(candidate, "candidate.json")

    updated = store.bulk_set_labels(
        ["run-baseline-001", "run-candidate-001"], "triaged"
    )

    assert updated == 2
    assert store.get_run("run-baseline-001")["label"] == "triaged"
    assert store.get_run("run-candidate-001")["label"] == "triaged"
    assert store.unreviewed_runs() == []


def test_store_bulk_labels_dedupes_and_skips_missing(tmp_path, baseline, candidate):
    store = TraceStore(tmp_path / "bulk.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(candidate, "candidate.json")

    updated = store.bulk_set_labels(
        ["run-baseline-001", "run-baseline-001", "run-not-here"], "golden"
    )

    assert updated == 1
    assert store.get_run("run-baseline-001")["label"] == "golden"


def test_store_bulk_clears_labels_with_empty_string(tmp_path, baseline):
    store = TraceStore(tmp_path / "bulk.db")
    store.ingest(baseline, "baseline.json")
    store.update_annotations("run-baseline-001", label="golden")

    assert store.bulk_set_labels(["run-baseline-001"], "") == 1
    assert store.get_run("run-baseline-001")["label"] == ""


def test_store_bulk_rejects_overlong_label(tmp_path, baseline):
    store = TraceStore(tmp_path / "bulk.db")
    store.ingest(baseline, "baseline.json")

    with pytest.raises(ValueError, match="label"):
        store.bulk_set_labels(["run-baseline-001"], "x" * 81)


def test_store_bulk_returns_zero_for_empty_list(tmp_path, baseline):
    store = TraceStore(tmp_path / "bulk.db")
    store.ingest(baseline, "baseline.json")

    assert store.bulk_set_labels([], "golden") == 0


def test_api_bulk_labels_review_runs(tmp_path, baseline, candidate):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())
    client.post("/api/traces", json=candidate.as_jsonable())

    response = client.post(
        "/api/review/labels",
        json={"run_ids": ["run-baseline-001", "run-candidate-001"], "label": "triaged"},
    )

    assert response.status_code == 200
    assert response.json()["updated"] == 2
    assert client.get("/api/review").json() == []


def test_api_bulk_label_clears_with_empty_label(tmp_path, baseline, candidate):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())
    client.post("/api/traces", json=candidate.as_jsonable())
    client.post(
        "/api/review/labels",
        json={"run_ids": ["run-baseline-001", "run-candidate-001"], "label": "triaged"},
    )

    response = client.post(
        "/api/review/labels",
        json={"run_ids": ["run-baseline-001", "run-candidate-001"], "label": ""},
    )

    assert response.json()["updated"] == 2
    assert len(client.get("/api/review").json()) == 2


def test_api_bulk_label_validates_payload(tmp_path, baseline):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())

    assert (
        client.post("/api/review/labels", json={"run_ids": [], "label": "x"}).status_code
        == 422
    )
    assert (
        client.post(
            "/api/review/labels", json={"run_ids": [""], "label": "x"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/review/labels",
            json={"run_ids": ["run-baseline-001"], "label": "x" * 81},
        ).status_code
        == 422
    )


def test_api_report_csv_returns_attachment(tmp_path, baseline, candidate):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())
    client.post("/api/traces", json=candidate.as_jsonable())

    response = client.get("/api/report", params={"format": "csv"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"] == 'attachment; filename="library-report.csv"'
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert [row["section"] for row in rows] == ["total", "source", "agent", "retention"]
    assert rows[0]["runs"] == "2"


def test_api_report_rejects_unknown_format(tmp_path, baseline):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())

    assert client.get("/api/report", params={"format": "xml"}).status_code == 400


def test_cli_review_labels_every_unreviewed_run(tmp_path, baseline, candidate, monkeypatch, capsys):
    store = TraceStore(tmp_path / "cli.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(candidate, "candidate.json")
    store.update_annotations("run-baseline-001", label="golden")

    monkeypatch.setattr(
        "sys.argv",
        ["atw", "--db", str(tmp_path / "cli.db"), "review", "--label", "triaged"],
    )
    main()

    result = json.loads(capsys.readouterr().out)
    assert result["label"] == "triaged"
    assert result["updated"] == 1
    assert store.get_run("run-candidate-001")["label"] == "triaged"


def test_cli_review_labels_specific_runs(tmp_path, baseline, candidate, monkeypatch, capsys):
    store = TraceStore(tmp_path / "cli.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(candidate, "candidate.json")

    monkeypatch.setattr(
        "sys.argv",
        [
            "atw",
            "--db",
            str(tmp_path / "cli.db"),
            "review",
            "--label",
            "golden",
            "--run-id",
            "run-baseline-001",
        ],
    )
    main()

    assert json.loads(capsys.readouterr().out)["updated"] == 1
    assert store.get_run("run-baseline-001")["label"] == "golden"
    assert store.get_run("run-candidate-001")["label"] == ""


def test_cli_review_run_id_requires_label(tmp_path, baseline, monkeypatch):
    store = TraceStore(tmp_path / "cli.db")
    store.ingest(baseline, "baseline.json")

    monkeypatch.setattr(
        "sys.argv",
        [
            "atw",
            "--db",
            str(tmp_path / "cli.db"),
            "review",
            "--run-id",
            "run-baseline-001",
        ],
    )
    with pytest.raises(SystemExit, match="--label"):
        main()


def test_cli_report_csv_prints_rows(tmp_path, baseline, candidate, monkeypatch, capsys):
    store = TraceStore(tmp_path / "cli.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(candidate, "candidate.json")

    monkeypatch.setattr(
        "sys.argv",
        ["atw", "--db", str(tmp_path / "cli.db"), "report", "--format", "csv"],
    )
    main()

    rows = list(csv.DictReader(io.StringIO(capsys.readouterr().out)))
    assert rows[0]["section"] == "total"
    assert rows[0]["runs"] == "2"
    assert rows[-1]["section"] == "retention"


def test_review_page_shows_bulk_label_form(tmp_path, baseline, candidate):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())
    client.post("/api/traces", json=candidate.as_jsonable())

    page = client.get("/review").text

    assert "bulk-label-form" in page
    assert "select-all" in page
    assert 'class="run-check"' in page


def test_report_page_shows_csv_download_link(tmp_path, baseline, candidate):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())
    client.post("/api/traces", json=candidate.as_jsonable())

    page = client.get("/report").text

    assert "/api/report?format=csv" in page
