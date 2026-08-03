import json
import socket

import pytest
from fastapi.testclient import TestClient

from agent_trace_workbench.cli import main
from agent_trace_workbench.collector import export_run_to_collector
from agent_trace_workbench.main import create_app
from agent_trace_workbench.storage import TraceStore
from agent_trace_workbench.telemetry import traces_url


def _closed_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def test_traces_url_appends_v1_traces_path():
    assert traces_url("http://127.0.0.1:4318") == "http://127.0.0.1:4318/v1/traces"
    assert traces_url("127.0.0.1:4318") == "http://127.0.0.1:4318/v1/traces"
    assert traces_url("http://localhost:4318/") == "http://localhost:4318/v1/traces"


def test_export_run_posts_valid_otlp_json(baseline, collector_server):
    endpoint, recorder = collector_server

    report = export_run_to_collector(baseline, endpoint)

    assert report.status == "accepted"
    assert report.run_id == "run-baseline-001"
    assert report.span_count == 4
    assert report.detail is None
    assert len(recorder.bodies) == 1
    path, body = recorder.bodies[0]
    assert path == "/v1/traces"
    payload = json.loads(body.decode("utf-8"))
    assert payload["resourceSpans"][0]["resource"]["attributes"][0]["key"] == "service.name"
    spans = payload["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert [span["name"] for span in spans] == [
        "agent.run",
        "search_catalog",
        "agent.plan",
        "get_inventory",
    ]


def test_export_run_reports_http_error(baseline, collector_server):
    endpoint, recorder = collector_server
    recorder.status = 500

    report = export_run_to_collector(baseline, endpoint)

    assert report.status == "failed"
    assert report.detail == "Collector returned HTTP 500"


def test_export_run_reports_connection_refused(baseline):
    endpoint = f"http://127.0.0.1:{_closed_port()}"

    report = export_run_to_collector(baseline, endpoint)

    assert report.status == "failed"
    assert report.detail == "Connection refused"


def test_cli_publish_sends_one_run(tmp_path, baseline, monkeypatch, capsys, collector_server):
    endpoint, recorder = collector_server
    store = TraceStore(tmp_path / "cli.db")
    store.ingest(baseline, "run_baseline.json")

    monkeypatch.setattr(
        "sys.argv",
        [
            "atw",
            "--db",
            str(tmp_path / "cli.db"),
            "publish",
            "run-baseline-001",
            "--endpoint",
            endpoint,
        ],
    )
    main()
    report = json.loads(capsys.readouterr().out)

    assert report["endpoint"] == endpoint
    assert report["exported_runs"][0]["status"] == "accepted"
    assert report["exported_runs"][0]["span_count"] == 4
    assert len(recorder.bodies) == 1


def test_cli_publish_sends_every_run(tmp_path, baseline, monkeypatch, capsys, collector_server):
    endpoint, recorder = collector_server
    store = TraceStore(tmp_path / "cli.db")
    store.ingest(baseline, "run_baseline.json")

    monkeypatch.setattr(
        "sys.argv", ["atw", "--db", str(tmp_path / "cli.db"), "publish", "--endpoint", endpoint]
    )
    main()
    report = json.loads(capsys.readouterr().out)

    assert len(report["exported_runs"]) == 1
    assert len(recorder.bodies) == 1


def test_cli_publish_requires_an_endpoint(tmp_path, baseline, monkeypatch, capsys):
    store = TraceStore(tmp_path / "cli.db")
    store.ingest(baseline, "run_baseline.json")

    monkeypatch.setattr("sys.argv", ["atw", "--db", str(tmp_path / "cli.db"), "publish"])
    with pytest.raises(SystemExit, match="endpoint"):
        main()


def test_cli_publish_missing_run_fails(tmp_path, monkeypatch, capsys, collector_server):
    endpoint, _recorder = collector_server

    monkeypatch.setattr(
        "sys.argv",
        ["atw", "--db", str(tmp_path / "cli.db"), "publish", "not-here", "--endpoint", endpoint],
    )
    with pytest.raises(SystemExit, match="Run not found"):
        main()


def test_api_publish_uses_request_endpoint(tmp_path, baseline, monkeypatch, collector_server):
    monkeypatch.delenv("ATW_OTEL_COLLECTOR_ENDPOINT", raising=False)
    endpoint, recorder = collector_server
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())

    response = client.post(
        "/api/runs/run-baseline-001/export/collector", json={"endpoint": endpoint}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "accepted"
    assert body["span_count"] == 4
    assert body["endpoint"] == endpoint
    assert len(recorder.bodies) == 1


def test_api_publish_uses_environment_endpoint(tmp_path, baseline, monkeypatch, collector_server):
    endpoint, recorder = collector_server
    monkeypatch.setenv("ATW_OTEL_COLLECTOR_ENDPOINT", endpoint)
    monkeypatch.setattr(
        "agent_trace_workbench.main.configure_telemetry", lambda: None
    )
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())

    response = client.post("/api/runs/run-baseline-001/export/collector")

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert len(recorder.bodies) == 1


def test_api_publish_without_endpoint_returns_400(tmp_path, baseline, monkeypatch):
    monkeypatch.delenv("ATW_OTEL_COLLECTOR_ENDPOINT", raising=False)
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())

    response = client.post("/api/runs/run-baseline-001/export/collector")

    assert response.status_code == 400
    assert "endpoint" in response.json()["detail"]


def test_api_publish_missing_run_returns_404(tmp_path, monkeypatch, collector_server):
    monkeypatch.delenv("ATW_OTEL_COLLECTOR_ENDPOINT", raising=False)
    endpoint, _recorder = collector_server
    client = TestClient(create_app(tmp_path / "api.db"))

    response = client.post(
        "/api/runs/not-here/export/collector", json={"endpoint": endpoint}
    )

    assert response.status_code == 404


def test_api_publish_failed_collector_returns_report(
    tmp_path, baseline, monkeypatch, collector_server
):
    monkeypatch.delenv("ATW_OTEL_COLLECTOR_ENDPOINT", raising=False)
    endpoint, recorder = collector_server
    recorder.status = 503
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())

    response = client.post(
        "/api/runs/run-baseline-001/export/collector", json={"endpoint": endpoint}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["detail"] == "Collector returned HTTP 503"
