import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from agent_trace_workbench.cli import main
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
    ingested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def test_store_updates_label_and_note(tmp_path, baseline):
    store = TraceStore(tmp_path / "annotations.db")
    store.ingest(baseline, "baseline.json")

    updated = store.update_annotations("run-baseline-001", label="v1", note="Looks good")

    assert updated["label"] == "v1"
    assert updated["note"] == "Looks good"
    assert store.get_run("run-baseline-001")["label"] == "v1"
    assert store.list_runs()[0]["label"] == "v1"


def test_store_update_returns_none_for_missing_run(tmp_path):
    store = TraceStore(tmp_path / "annotations.db")

    assert store.update_annotations("missing", label="v1") is None


def test_store_update_requires_an_annotation(tmp_path, baseline):
    store = TraceStore(tmp_path / "annotations.db")
    store.ingest(baseline, "baseline.json")

    with pytest.raises(ValueError, match="Provide a label"):
        store.update_annotations("run-baseline-001")


def test_store_rejects_overlong_annotations(tmp_path, baseline):
    store = TraceStore(tmp_path / "annotations.db")
    store.ingest(baseline, "baseline.json")

    with pytest.raises(ValueError, match="label"):
        store.update_annotations("run-baseline-001", label="x" * 81)
    with pytest.raises(ValueError, match="note"):
        store.update_annotations("run-baseline-001", note="y" * 2001)


def test_store_preserves_annotations_on_reingest(tmp_path, baseline):
    store = TraceStore(tmp_path / "annotations.db")
    store.ingest(baseline, "baseline.json")
    store.update_annotations("run-baseline-001", label="keep-me", note="reviewed")

    store.ingest(baseline, "baseline-again.json")

    run = store.get_run("run-baseline-001")
    assert run["label"] == "keep-me"
    assert run["note"] == "reviewed"
    assert run["source_name"] == "baseline-again.json"


def test_store_clears_annotations_with_empty_strings(tmp_path, baseline):
    store = TraceStore(tmp_path / "annotations.db")
    store.ingest(baseline, "baseline.json")
    store.update_annotations("run-baseline-001", label="temp", note="wip")

    store.update_annotations("run-baseline-001", label="", note="")

    run = store.get_run("run-baseline-001")
    assert run["label"] == ""
    assert run["note"] == ""


def test_store_migrates_legacy_database_without_annotation_columns(tmp_path, baseline):
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
    assert {"label", "note"} <= columns
    run = store.get_run("run-baseline-001")
    assert run["label"] == ""
    assert run["note"] == ""


def test_store_migration_is_idempotent_across_openings(tmp_path, baseline):
    database = tmp_path / "legacy.db"
    legacy = sqlite3.connect(str(database))
    legacy.executescript(_LEGACY_SCHEMA)
    legacy.commit()
    legacy.close()

    TraceStore(database).ingest(baseline, "baseline.json")
    second = TraceStore(database)

    assert second.get_run("run-baseline-001")["label"] == ""


def test_store_migration_fills_missing_columns_only(tmp_path, baseline):
    database = tmp_path / "partial.db"
    legacy = sqlite3.connect(str(database))
    legacy.executescript(
        _LEGACY_SCHEMA.replace(
            "    raw_json TEXT NOT NULL,\n"
            "    ingested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP\n);",
            "    raw_json TEXT NOT NULL,\n"
            "    ingested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,\n"
            "    label TEXT NOT NULL DEFAULT ''\n);",
        )
    )
    legacy.commit()
    legacy.close()

    store = TraceStore(database)
    store.ingest(baseline, "baseline.json")

    columns = {
        str(row[1]) for row in sqlite3.connect(str(database)).execute("PRAGMA table_info(runs)")
    }
    assert "note" in columns
    assert store.get_run("run-baseline-001")["note"] == ""


def test_search_matches_label(tmp_path, baseline, candidate):
    store = TraceStore(tmp_path / "annotations.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(candidate, "candidate.json")
    store.update_annotations("run-baseline-001", label="golden")

    results = store.search_runs("golden")

    assert [run["run_id"] for run in results] == ["run-baseline-001"]


def test_api_updates_annotations(tmp_path, baseline):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())

    response = client.patch(
        "/api/runs/run-baseline-001/annotations",
        json={"label": "golden", "note": "reference"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["label"] == "golden"
    assert body["note"] == "reference"
    assert client.get("/api/runs/run-baseline-001").json()["label"] == "golden"


def test_api_annotations_404_for_missing_run(tmp_path):
    client = TestClient(create_app(tmp_path / "api.db"))

    response = client.patch("/api/runs/missing/annotations", json={"label": "v1"})

    assert response.status_code == 404


def test_api_rejects_empty_annotation_body(tmp_path, baseline):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())

    assert client.patch("/api/runs/run-baseline-001/annotations", json={}).status_code == 422


def test_cli_annotate_sets_label_and_note(tmp_path, baseline, monkeypatch, capsys):
    store = TraceStore(tmp_path / "cli.db")
    store.ingest(baseline, "baseline.json")

    monkeypatch.setattr(
        "sys.argv",
        [
            "atw",
            "--db",
            str(tmp_path / "cli.db"),
            "annotate",
            "run-baseline-001",
            "--label",
            "golden",
            "--note",
            "reference run",
        ],
    )
    main()

    result = json.loads(capsys.readouterr().out)
    assert result["label"] == "golden"
    assert result["note"] == "reference run"


def test_cli_annotate_clears_existing_annotations(tmp_path, baseline, monkeypatch, capsys):
    store = TraceStore(tmp_path / "cli.db")
    store.ingest(baseline, "baseline.json")
    store.update_annotations("run-baseline-001", label="golden", note="reference")

    monkeypatch.setattr(
        "sys.argv",
        ["atw", "--db", str(tmp_path / "cli.db"), "annotate", "run-baseline-001", "--clear"],
    )
    main()

    result = json.loads(capsys.readouterr().out)
    assert result["label"] == ""
    assert result["note"] == ""


def test_cli_annotate_requires_an_action(tmp_path, baseline, monkeypatch):
    store = TraceStore(tmp_path / "cli.db")
    store.ingest(baseline, "baseline.json")

    monkeypatch.setattr(
        "sys.argv",
        ["atw", "--db", str(tmp_path / "cli.db"), "annotate", "run-baseline-001"],
    )
    with pytest.raises(SystemExit, match="--label"):
        main()


def test_dashboard_shows_label_badge(tmp_path, baseline):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())
    client.patch("/api/runs/run-baseline-001/annotations", json={"label": "golden"})

    assert "golden" in client.get("/").text


def test_run_page_shows_annotation_form_and_note(tmp_path, baseline):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())
    client.patch(
        "/api/runs/run-baseline-001/annotations",
        json={"label": "golden", "note": "reference run"},
    )

    page = client.get("/runs/run-baseline-001").text

    assert "annotations-form" in page
    assert "golden" in page
    assert "reference run" in page
