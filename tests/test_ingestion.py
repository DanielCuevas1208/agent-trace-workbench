import json

from agent_trace_workbench.ingestion import DirectoryWatcher
from agent_trace_workbench.storage import TraceStore


def test_watcher_ingests_valid_files_and_reports_file_errors(tmp_path, baseline):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "01-valid.json").write_text(
        json.dumps(baseline.as_jsonable()), encoding="utf-8"
    )
    (inbox / "02-invalid.json").write_text("{broken", encoding="utf-8")
    (inbox / "03-schema.json").write_text(
        json.dumps({"trace_id": "incomplete"}), encoding="utf-8"
    )

    watcher = DirectoryWatcher(TraceStore(tmp_path / "traces.db"), inbox)
    report = watcher.scan()

    assert report.discovered_files == 3
    assert report.processed_files == 3
    assert report.ingested_files == 1
    assert [run["run_id"] for run in report.ingested_runs] == ["run-baseline-001"]
    assert [(issue.source_name, issue.kind) for issue in report.issues] == [
        ("02-invalid.json", "invalid_json"),
        ("03-schema.json", "schema_error"),
    ]
    second_report = watcher.scan()
    assert len(second_report.issues) == 2
    assert second_report.processed_files == 0
    assert second_report.skipped_files == 3

    repaired = baseline.as_jsonable()
    repaired["run_id"] = "run-repaired-001"
    (inbox / "02-invalid.json").write_text(
        json.dumps(repaired), encoding="utf-8"
    )
    repaired_report = watcher.scan()
    assert repaired_report.processed_files == 1
    assert repaired_report.ingested_files == 1
    assert [issue.source_name for issue in repaired_report.issues] == ["03-schema.json"]


def test_watcher_skips_unchanged_files_on_the_next_scan(tmp_path, baseline):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "trace.json").write_text(
        json.dumps(baseline.as_jsonable()), encoding="utf-8"
    )
    watcher = DirectoryWatcher(TraceStore(tmp_path / "traces.db"), inbox)

    watcher.scan()
    report = watcher.scan()

    assert report.discovered_files == 1
    assert report.processed_files == 0
    assert report.skipped_files == 1
    assert report.ingested_files == 0
    assert report.issues == []
