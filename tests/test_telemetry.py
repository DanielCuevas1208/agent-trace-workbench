import http.server
import json
import threading
from http.server import BaseHTTPRequestHandler

import pytest
from fastapi.testclient import TestClient
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor

from agent_trace_workbench.cli import main
from agent_trace_workbench.main import create_app
from agent_trace_workbench.telemetry import (
    build_span_processors,
    configure_telemetry,
    shutdown_telemetry,
    telemetry_info,
)


class _CollectorHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        self.server.received.append((self.path, dict(self.headers), body))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *_args: object) -> None:
        pass


@pytest.fixture
def collector():
    server = http.server.HTTPServer(("127.0.0.1", 0), _CollectorHandler)
    server.received = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


@pytest.fixture(autouse=True)
def _reset_telemetry():
    yield
    shutdown_telemetry()


def test_telemetry_info_off_by_default(monkeypatch):
    monkeypatch.delenv("ATW_OTEL_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("ATW_OTEL_CONSOLE", raising=False)

    info = telemetry_info()

    assert info["service_name"] == "agent-trace-workbench"
    assert info["otlp_exporter"] is False
    assert info["console_exporter"] is False
    assert info["otlp_endpoint"] is None


def test_telemetry_info_reads_workbench_endpoint(monkeypatch):
    monkeypatch.setenv("ATW_OTEL_OTLP_ENDPOINT", "http://localhost:4318")

    info = telemetry_info()

    assert info["otlp_exporter"] is True
    assert info["otlp_endpoint"] == "http://localhost:4318/v1/traces"


def test_telemetry_info_reads_standard_endpoint(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")

    info = telemetry_info()

    assert info["otlp_exporter"] is True
    assert info["otlp_endpoint"] == "http://localhost:4318/v1/traces"


def test_telemetry_info_prefers_workbench_endpoint(monkeypatch):
    monkeypatch.setenv("ATW_OTEL_OTLP_ENDPOINT", "http://127.0.0.1:9999")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")

    assert telemetry_info()["otlp_endpoint"] == "http://127.0.0.1:9999/v1/traces"


def test_telemetry_info_keeps_full_trace_path(monkeypatch):
    monkeypatch.setenv("ATW_OTEL_OTLP_ENDPOINT", "http://localhost:4318/v1/traces")

    assert telemetry_info()["otlp_endpoint"] == "http://localhost:4318/v1/traces"


def test_telemetry_info_reports_console_exporter(monkeypatch):
    monkeypatch.setenv("ATW_OTEL_CONSOLE", "1")

    assert telemetry_info()["console_exporter"] is True


def test_build_span_processors_defaults_empty():
    assert build_span_processors() == []


def test_build_span_processors_console(monkeypatch):
    monkeypatch.setenv("ATW_OTEL_CONSOLE", "1")

    processors = build_span_processors()

    assert len(processors) == 1
    assert isinstance(processors[0], SimpleSpanProcessor)


def test_build_span_processors_otlp(monkeypatch):
    monkeypatch.setenv("ATW_OTEL_OTLP_ENDPOINT", "http://localhost:4318")

    processors = build_span_processors()

    assert len(processors) == 1
    assert isinstance(processors[0], BatchSpanProcessor)


def test_configure_telemetry_is_noop_without_env(monkeypatch):
    monkeypatch.delenv("ATW_OTEL_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("ATW_OTEL_CONSOLE", raising=False)

    configure_telemetry()


def test_otlp_export_sends_spans_to_local_collector(monkeypatch, collector):
    port = collector.server_address[1]
    monkeypatch.setenv("ATW_OTEL_OTLP_ENDPOINT", f"http://127.0.0.1:{port}")

    processors = build_span_processors()
    assert len(processors) == 1
    provider = TracerProvider()
    provider.add_span_processor(processors[0])
    tracer = provider.get_tracer("atw.test")
    with tracer.start_as_current_span("telemetry.check") as span:
        span.set_attribute("check", "value")
    provider.force_flush()

    assert len(collector.received) == 1
    path, headers, body = collector.received[0]
    assert path == "/v1/traces"
    assert headers.get("Content-Type") == "application/x-protobuf"
    request = ExportTraceServiceRequest()
    request.ParseFromString(body)
    names = [
        span.name
        for resource_spans in request.resource_spans
        for scope_spans in resource_spans.scope_spans
        for span in scope_spans.spans
    ]
    assert "telemetry.check" in names


def test_otlp_export_sends_multiple_spans_in_one_batch(monkeypatch, collector):
    port = collector.server_address[1]
    monkeypatch.setenv("ATW_OTEL_OTLP_ENDPOINT", f"http://127.0.0.1:{port}")

    provider = TracerProvider()
    provider.add_span_processor(build_span_processors()[0])
    tracer = provider.get_tracer("atw.test")
    for name in ("step.one", "step.two"):
        with tracer.start_as_current_span(name):
            pass
    provider.force_flush()

    assert len(collector.received) >= 1


def test_cli_telemetry_prints_configuration(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ATW_OTEL_OTLP_ENDPOINT", "http://localhost:4318")
    monkeypatch.setattr(
        "sys.argv", ["atw", "--db", str(tmp_path / "cli.db"), "telemetry"]
    )

    main()
    info = json.loads(capsys.readouterr().out)

    assert info["otlp_exporter"] is True
    assert info["otlp_endpoint"] == "http://localhost:4318/v1/traces"


def test_api_telemetry_reports_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv("ATW_OTEL_OTLP_ENDPOINT", "http://localhost:4318")
    monkeypatch.setattr("agent_trace_workbench.main.configure_telemetry", lambda: None)
    client = TestClient(create_app(tmp_path / "api.db"))

    response = client.get("/api/telemetry")

    assert response.status_code == 200
    body = response.json()
    assert body["otlp_exporter"] is True
    assert body["otlp_endpoint"] == "http://localhost:4318/v1/traces"
    assert body["service_name"] == "agent-trace-workbench"


def test_telemetry_page_renders_when_off(tmp_path):
    client = TestClient(create_app(tmp_path / "api.db"))

    response = client.get("/telemetry")

    assert response.status_code == 200
    assert "Export is off" in response.text
    assert "OTLP" in response.text
