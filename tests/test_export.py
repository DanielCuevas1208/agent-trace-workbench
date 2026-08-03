import json

import pytest
from fastapi.testclient import TestClient

from agent_trace_workbench.cli import main
from agent_trace_workbench.main import create_app
from agent_trace_workbench.models import TraceDocument
from agent_trace_workbench.otlp import parse_otlp_json, trace_to_otlp_json
from agent_trace_workbench.storage import TraceStore


def _seed(store: TraceStore, baseline, candidate) -> None:
    store.ingest(baseline, "run_baseline.json")
    if candidate is not None:
        store.ingest(candidate, "run_candidate.json")


def test_api_export_json_returns_portable_document(tmp_path, baseline):
    store = TraceStore(tmp_path / "api.db")
    _seed(store, baseline, None)

    client = TestClient(create_app(tmp_path / "api.db"))
    response = client.get("/api/runs/run-baseline-001/export")

    assert response.status_code == 200
    disposition = response.headers["content-disposition"]
    assert disposition.startswith('attachment; filename="run-baseline-001.json"')
    body = response.json()
    assert body["run_id"] == "run-baseline-001"
    assert body["trace_id"] == "trace-demo-001"


def test_api_export_otlp_returns_otlp_json(tmp_path, baseline):
    store = TraceStore(tmp_path / "api.db")
    _seed(store, baseline, None)

    client = TestClient(create_app(tmp_path / "api.db"))
    response = client.get("/api/runs/run-baseline-001/export", params={"format": "otlp"})

    assert response.status_code == 200
    body = response.json()
    resource_spans = body["resourceSpans"]
    assert resource_spans[0]["resource"]["attributes"][0]["key"] == "service.name"
    assert resource_spans[0]["scopeSpans"][0]["spans"][0]["name"] == "agent.run"


def test_api_export_rejects_unknown_format(tmp_path, baseline):
    store = TraceStore(tmp_path / "api.db")
    _seed(store, baseline, None)

    client = TestClient(create_app(tmp_path / "api.db"))
    response = client.get("/api/runs/run-baseline-001/export", params={"format": "xml"})

    assert response.status_code == 400


def test_api_export_missing_run_returns_404(tmp_path):
    client = TestClient(create_app(tmp_path / "api.db"))

    assert client.get("/api/runs/nope/export").status_code == 404


def test_api_otlp_import_stores_run(tmp_path, baseline):
    store = TraceStore(tmp_path / "api.db")
    _seed(store, baseline, None)

    client = TestClient(create_app(tmp_path / "api.db"))
    response = client.post("/api/otlp/traces", json=trace_to_otlp_json(baseline))

    assert response.status_code == 201
    runs = response.json()
    assert len(runs) == 1
    assert runs[0]["run_id"] == "run-baseline-001"
    assert client.get("/api/runs/run-baseline-001").json()["spans"][0]["name"] == "agent.run"


def test_api_otlp_import_rejects_empty_payload(tmp_path):
    client = TestClient(create_app(tmp_path / "api.db"))

    response = client.post("/api/otlp/traces", json={"resourceSpans": []})

    assert response.status_code == 400


def test_cli_export_json_file_reingests_cleanly(tmp_path, baseline, monkeypatch, capsys):
    store = TraceStore(tmp_path / "cli.db")
    _seed(store, baseline, None)

    monkeypatch.setattr(
        "sys.argv",
        [
            "atw",
            "--db",
            str(tmp_path / "cli.db"),
            "export",
            "run-baseline-001",
            "--output",
            str(tmp_path),
        ],
    )
    main()
    report = json.loads(capsys.readouterr().out)
    assert report["exported"][0]["path"].endswith("run-baseline-001.json")

    restored = TraceDocument.model_validate_json(
        (tmp_path / "run-baseline-001.json").read_text(encoding="utf-8")
    )
    assert restored.model_dump(mode="json") == baseline.model_dump(mode="json")


def test_cli_export_otlp_round_trips_through_import(
    tmp_path, baseline, candidate, monkeypatch, capsys
):
    store = TraceStore(tmp_path / "cli.db")
    _seed(store, baseline, candidate)

    monkeypatch.setattr(
        "sys.argv",
        [
            "atw",
            "--db",
            str(tmp_path / "cli.db"),
            "export",
            "run-candidate-001",
            "--format",
            "otlp",
            "--output",
            str(tmp_path),
        ],
    )
    main()
    capsys.readouterr()
    source = tmp_path / "run-candidate-001.otlp.json"

    monkeypatch.setattr(
        "sys.argv",
        ["atw", "--db", str(tmp_path / "cli-import.db"), "import-otlp", str(source)],
    )
    main()
    report = json.loads(capsys.readouterr().out)
    assert report["imported_runs"] == 1
    assert report["runs"][0]["run_id"] == "run-candidate-001"

    imported_store = TraceStore(tmp_path / "cli-import.db")
    trace = imported_store.get_trace("run-candidate-001")
    assert trace is not None
    assert trace.tool_spans()[-1].tool_call.error == "reservation window expired"


def test_cli_export_all_writes_one_file_per_run(tmp_path, baseline, candidate, monkeypatch, capsys):
    store = TraceStore(tmp_path / "cli.db")
    _seed(store, baseline, candidate)

    monkeypatch.setattr(
        "sys.argv",
        ["atw", "--db", str(tmp_path / "cli.db"), "export", "--output", str(tmp_path / "out")],
    )
    main()
    report = json.loads(capsys.readouterr().out)

    assert {item["run_id"] for item in report["exported"]} == {
        "run-baseline-001",
        "run-candidate-001",
    }
    assert (tmp_path / "out" / "run-baseline-001.json").exists()
    assert (tmp_path / "out" / "run-candidate-001.json").exists()


def test_cli_import_otlp_derives_run_id(tmp_path, monkeypatch, capsys):
    payload = {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": "inventory"}}
                    ]
                },
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": "aaaa0000000000000000000000000000",
                                "spanId": "1111000000000000",
                                "name": "check",
                                "startTimeUnixNano": "1544712660000000000",
                                "endTimeUnixNano": "1544712661000000000",
                                "status": {"code": 1},
                            }
                        ]
                    }
                ],
            }
        ]
    }
    source = tmp_path / "inventory.otlp.json"
    source.write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.setattr(
        "sys.argv",
        ["atw", "--db", str(tmp_path / "cli.db"), "import-otlp", str(source)],
    )
    main()
    report = json.loads(capsys.readouterr().out)

    assert report["imported_runs"] == 1
    assert report["runs"][0]["run_id"] == "inventory-aaaa00000000"


def test_cli_export_missing_run_raises(tmp_path, monkeypatch, capsys):
    store = TraceStore(tmp_path / "cli.db")
    store.initialize()

    monkeypatch.setattr(
        "sys.argv",
        ["atw", "--db", str(tmp_path / "cli.db"), "export", "nope", "--output", str(tmp_path)],
    )
    with pytest.raises(SystemExit):
        main()


def test_parse_otlp_imports_multiple_resource_groups(tmp_path, baseline, candidate):
    payload = {
        "resourceSpans": [
            trace_to_otlp_json(baseline)["resourceSpans"][0],
            trace_to_otlp_json(candidate)["resourceSpans"][0],
        ]
    }

    store = TraceStore(tmp_path / "cli.db")
    documents = parse_otlp_json(payload)
    for trace in documents:
        store.ingest(trace, "multi.otlp.json")

    assert {run["run_id"] for run in store.list_runs()} == {
        "run-baseline-001",
        "run-candidate-001",
    }
