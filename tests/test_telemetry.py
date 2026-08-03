import json

import pytest
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from agent_trace_workbench import telemetry as tel
from agent_trace_workbench.cli import main

OTEL_ENV_VARS = (
    "ATW_OTEL_ENDPOINT",
    "ATW_OTEL_CONSOLE",
    "ATW_OTEL_SERVICE_NAME",
    "ATW_OTEL_BATCH_TIMEOUT_MS",
)


@pytest.fixture
def isolated_provider():
    """Give each test a fresh global tracer provider.

    The OpenTelemetry SDK only installs a tracer provider once. Tests
    reset the module-level provider and its once-guard so each test can
    install the provider it needs.
    """
    import opentelemetry.trace as otel_module

    original_provider = otel_module._TRACER_PROVIDER
    original_once = otel_module._TRACER_PROVIDER_SET_ONCE
    otel_module._TRACER_PROVIDER = None
    otel_module._TRACER_PROVIDER_SET_ONCE = otel_module.Once()
    yield
    otel_module._TRACER_PROVIDER = original_provider
    otel_module._TRACER_PROVIDER_SET_ONCE = original_once


def test_read_telemetry_config_defaults_inactive(monkeypatch):
    for name in OTEL_ENV_VARS:
        monkeypatch.delenv(name, raising=False)

    config = tel.read_telemetry_config()

    assert config.active is False
    assert config.service_name == "agent-trace-workbench"
    assert config.endpoint is None
    assert config.console is False
    assert config.batch_timeout_ms == 5000


def test_read_telemetry_config_applies_environment(monkeypatch):
    monkeypatch.setenv("ATW_OTEL_ENDPOINT", "http://localhost:4318")
    monkeypatch.setenv("ATW_OTEL_CONSOLE", "1")
    monkeypatch.setenv("ATW_OTEL_SERVICE_NAME", "local-agent")
    monkeypatch.setenv("ATW_OTEL_BATCH_TIMEOUT_MS", "750")

    config = tel.read_telemetry_config()

    assert config.active is True
    assert config.endpoint == "http://localhost:4318"
    assert config.console is True
    assert config.service_name == "local-agent"
    assert config.batch_timeout_ms == 750


def test_read_telemetry_config_rejects_bad_timeout(monkeypatch):
    monkeypatch.setenv("ATW_OTEL_BATCH_TIMEOUT_MS", "soon")

    with pytest.raises(ValueError):
        tel.read_telemetry_config()


def test_build_tracer_provider_returns_none_when_inactive():
    assert tel.build_tracer_provider(tel.TelemetryConfig()) is None


def test_build_tracer_provider_sets_service_name():
    exporter = InMemorySpanExporter()
    provider = tel.build_tracer_provider(
        tel.TelemetryConfig(endpoint="http://localhost:4318"),
        otlp_exporter=exporter,
        otlp_processor_factory=SimpleSpanProcessor,
    )

    assert provider.resource.attributes["service.name"] == "agent-trace-workbench"
    provider.shutdown()


def test_spans_flow_to_configured_exporter():
    exporter = InMemorySpanExporter()
    provider = tel.build_tracer_provider(
        tel.TelemetryConfig(endpoint="http://localhost:4318"),
        otlp_exporter=exporter,
        otlp_processor_factory=SimpleSpanProcessor,
    )

    tracer = provider.get_tracer("verify")
    with tracer.start_as_current_span("storage.ingest", attributes={"run.id": "r1"}):
        pass

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == "storage.ingest"
    assert spans[0].attributes["run.id"] == "r1"
    provider.shutdown()


def test_configure_telemetry_installs_one_provider(isolated_provider):
    exporter = InMemorySpanExporter()
    config = tel.TelemetryConfig(endpoint="http://localhost:4318")

    assert tel.configure_telemetry(
        config, otlp_exporter=exporter, otlp_processor_factory=SimpleSpanProcessor
    ) is True
    provider = otel_trace.get_tracer_provider()
    assert isinstance(provider, TracerProvider)

    assert tel.configure_telemetry(
        config, otlp_exporter=exporter, otlp_processor_factory=SimpleSpanProcessor
    ) is True
    assert otel_trace.get_tracer_provider() is provider
    provider.shutdown()


def test_configure_telemetry_stays_off_when_inactive(isolated_provider):
    assert tel.configure_telemetry(tel.TelemetryConfig()) is False
    assert not isinstance(otel_trace.get_tracer_provider(), TracerProvider)


def test_traced_operation_records_spans(isolated_provider):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    otel_trace.set_tracer_provider(provider)

    with tel.traced_operation("verify.replay", {"run.id": "r1"}):
        pass

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == "verify.replay"
    assert spans[0].attributes["run.id"] == "r1"
    provider.shutdown()


def test_traced_operation_marks_failed_spans(isolated_provider):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    otel_trace.set_tracer_provider(provider)

    with pytest.raises(ValueError):
        with tel.traced_operation("verify.failure"):
            raise ValueError("boom")

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].status.status_code == StatusCode.ERROR
    provider.shutdown()


def test_traces_path_normalises_collector_endpoint():
    assert tel._traces_path("http://localhost:4318") == "http://localhost:4318/v1/traces"
    assert tel._traces_path("http://collector:4318/") == "http://collector:4318/v1/traces"
    assert (
        tel._traces_path("http://localhost:4318/v1/traces") == "http://localhost:4318/v1/traces"
    )


def test_otlp_exporter_targets_v1_traces():
    exporter = tel._otlp_exporter("http://localhost:4318")

    assert exporter._endpoint == "http://localhost:4318/v1/traces"
    assert callable(exporter.export)
    assert callable(exporter.shutdown)
    exporter.shutdown()


def test_cli_telemetry_reports_inactive_config(monkeypatch, tmp_path, capsys):
    for name in OTEL_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("sys.argv", ["atw", "--db", str(tmp_path / "cli.db"), "telemetry"])

    main()
    status = json.loads(capsys.readouterr().out)

    assert status["active"] is False
    assert status["service_name"] == "agent-trace-workbench"
    assert status["endpoint"] is None
    assert status["batch_timeout_ms"] == 5000


def test_cli_telemetry_reports_active_config(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("ATW_OTEL_ENDPOINT", "http://localhost:4318")
    monkeypatch.setattr("sys.argv", ["atw", "--db", str(tmp_path / "cli.db"), "telemetry"])

    main()
    status = json.loads(capsys.readouterr().out)

    assert status["active"] is True
    assert status["endpoint"] == "http://localhost:4318"


def test_api_telemetry_reports_inactive_by_default(tmp_path, monkeypatch, isolated_provider):
    from fastapi.testclient import TestClient

    from agent_trace_workbench.main import create_app

    for name in OTEL_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    client = TestClient(create_app(tmp_path / "telemetry.db"))

    status = client.get("/api/telemetry").json()

    assert status["active"] is False
    assert status["service_name"] == "agent-trace-workbench"
    assert not isinstance(otel_trace.get_tracer_provider(), TracerProvider)


def test_api_telemetry_reports_active_configuration(
    tmp_path, monkeypatch, isolated_provider
):
    from fastapi.testclient import TestClient

    from agent_trace_workbench.main import create_app

    monkeypatch.setenv("ATW_OTEL_ENDPOINT", "http://localhost:4318")
    monkeypatch.setattr(tel, "_otlp_exporter", lambda _endpoint: InMemorySpanExporter())
    client = TestClient(create_app(tmp_path / "telemetry.db"))

    status = client.get("/api/telemetry").json()

    assert status["active"] is True
    assert status["endpoint"] == "http://localhost:4318"
    assert status["batch_timeout_ms"] == 5000
    provider = otel_trace.get_tracer_provider()
    assert isinstance(provider, TracerProvider)
    provider.shutdown()
