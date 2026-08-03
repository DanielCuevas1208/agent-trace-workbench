import json
import sqlite3

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
