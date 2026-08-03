import json

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

from agent_trace_workbench import __version__
from agent_trace_workbench.telemetry import OtlpJsonSpanExporter, configure_telemetry


def _make_span(name="storage.ingest", attribute=None):
    provider = TracerProvider()
    tracer = provider.get_tracer("agent-trace-workbench", "0.8.0")
    span = tracer.start_span(name)
    if attribute is not None:
        span.set_attribute(*attribute)
    span.end()
    return span


def test_exporter_posts_spans_as_otlp_json(collector_server):
    endpoint, recorder = collector_server
    exporter = OtlpJsonSpanExporter(endpoint)
    span = _make_span("storage.ingest", ("run.id", "run-test"))

    result = exporter.export([span])

    assert result is not None
    assert len(recorder.bodies) == 1
    path, body = recorder.bodies[0]
    assert path == "/v1/traces"
    payload = json.loads(body.decode("utf-8"))
    resource = payload["resourceSpans"][0]["resource"]["attributes"]
    by_key = {item["key"]: item["value"] for item in resource}
    assert by_key["service.name"]["stringValue"] == "agent-trace-workbench"
    assert by_key["service.version"]["stringValue"] == __version__
    spans = payload["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert spans[0]["name"] == "storage.ingest"
    assert len(spans[0]["traceId"]) == 32
    assert len(spans[0]["spanId"]) == 16
    assert spans[0]["status"] == {"code": 0}
    attributes = {item["key"]: item["value"] for item in spans[0]["attributes"]}
    assert attributes["run.id"]["stringValue"] == "run-test"


def test_exporter_skips_empty_batch(collector_server):
    endpoint, recorder = collector_server
    exporter = OtlpJsonSpanExporter(endpoint)

    result = exporter.export([])

    assert result is not None
    assert recorder.bodies == []


def test_exporter_reports_failure_on_http_error(collector_server):
    endpoint, recorder = collector_server
    recorder.status = 500
    exporter = OtlpJsonSpanExporter(endpoint)
    span = _make_span("storage.ingest")

    result = exporter.export([span])

    assert result is not None
    assert len(recorder.bodies) == 1


def test_exporter_does_not_raise_when_collector_is_down():
    exporter = OtlpJsonSpanExporter("http://127.0.0.1:1", timeout_seconds=0.1)

    result = exporter.export([_make_span("storage.ingest")])

    assert result is not None


def test_exporter_includes_parent_span_link(collector_server):
    endpoint, recorder = collector_server
    exporter = OtlpJsonSpanExporter(endpoint)
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("agent-trace-workbench", "0.8.0")
    with tracer.start_as_current_span("replay.run"):
        child = tracer.start_span("handler.execute")
        child.end()

    spans = []
    for _path, body in recorder.bodies:
        payload = json.loads(body.decode("utf-8"))
        spans.extend(payload["resourceSpans"][0]["scopeSpans"][0]["spans"])
    by_name = {span["name"]: span for span in spans}
    assert by_name["handler.execute"]["parentSpanId"] == by_name["replay.run"]["spanId"]


def test_configure_telemetry_returns_early_without_settings(monkeypatch):
    monkeypatch.delenv("ATW_OTEL_CONSOLE", raising=False)
    monkeypatch.delenv("ATW_OTEL_COLLECTOR_ENDPOINT", raising=False)

    assert configure_telemetry() is None


def test_configure_telemetry_ignores_unknown_exporter_settings(monkeypatch):
    monkeypatch.setenv("ATW_OTEL_COLLECTOR_ENDPOINT", "")
    monkeypatch.setenv("ATW_OTEL_CONSOLE", "0")

    assert configure_telemetry() is None


@pytest.mark.parametrize(
    "endpoint,expected",
    [
        ("http://127.0.0.1:4318", "http://127.0.0.1:4318/v1/traces"),
        ("127.0.0.1:4318", "http://127.0.0.1:4318/v1/traces"),
        ("http://localhost:4318/", "http://localhost:4318/v1/traces"),
    ],
)
def test_exporter_uses_traces_path(endpoint, expected):
    assert OtlpJsonSpanExporter(endpoint)._url == expected
