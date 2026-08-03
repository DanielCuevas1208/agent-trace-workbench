"""Local OpenTelemetry integration for workbench operations.

The workbench records spans for its own operations. It keeps that
telemetry local by default. Set ATW_OTEL_CONSOLE=1 to print spans to
the console. Set ATW_OTEL_ENDPOINT to send spans to a local
OpenTelemetry collector over OTLP HTTP.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterator

try:
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import (
        BatchSpanProcessor,
        ConsoleSpanExporter,
        SimpleSpanProcessor,
    )
    from opentelemetry.trace import SpanKind, Status, StatusCode
except ImportError:  # pragma: no cover - used only when optional local packages are unavailable
    Resource = None
    TracerProvider = None
    BatchSpanProcessor = None
    ConsoleSpanExporter = None
    SimpleSpanProcessor = None

    class _NoopSpan:
        def __enter__(self) -> "_NoopSpan":
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def record_exception(self, _exception: Exception) -> None:
            return None

        def set_status(self, _status: Any) -> None:
            return None

    class _NoopTracer:
        def start_as_current_span(self, *_args: Any, **_kwargs: Any) -> _NoopSpan:
            return _NoopSpan()

    class _NoopTrace:
        def get_tracer(self, *_args: Any, **_kwargs: Any) -> _NoopTracer:
            return _NoopTracer()

        def get_tracer_provider(self) -> None:
            return None

        def set_tracer_provider(self, _provider: Any) -> None:
            return None

    class _NoopSpanKind:
        INTERNAL = "INTERNAL"

    class _NoopStatusCode:
        ERROR = "ERROR"

    class _NoopStatus:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

    trace = _NoopTrace()
    SpanKind = _NoopSpanKind()
    Status = _NoopStatus
    StatusCode = _NoopStatusCode()


SERVICE_NAME = "agent-trace-workbench"
_OTLP_TRACES_PATH = "v1/traces"


@dataclass(frozen=True)
class TelemetryConfig:
    """Exporter settings read from the local environment."""

    service_name: str = SERVICE_NAME
    endpoint: str | None = None
    console: bool = False
    batch_timeout_ms: int = 5000

    @property
    def active(self) -> bool:
        """Return True when at least one exporter is configured."""
        return self.endpoint is not None or self.console


def read_telemetry_config() -> TelemetryConfig:
    """Read telemetry settings from the environment.

    ATW_OTEL_ENDPOINT names the local collector. ATW_OTEL_SERVICE_NAME
    sets the resource service name. ATW_OTEL_BATCH_TIMEOUT_MS controls
    how often the batched exporter flushes.
    """

    return TelemetryConfig(
        service_name=os.getenv("ATW_OTEL_SERVICE_NAME") or SERVICE_NAME,
        endpoint=os.getenv("ATW_OTEL_ENDPOINT") or None,
        console=os.getenv("ATW_OTEL_CONSOLE") == "1",
        batch_timeout_ms=_env_int("ATW_OTEL_BATCH_TIMEOUT_MS", 5000),
    )


def telemetry_status() -> dict[str, Any]:
    """Return a JSON-safe summary of the active telemetry settings."""

    config = read_telemetry_config()
    return {
        "active": config.active,
        "service_name": config.service_name,
        "endpoint": config.endpoint,
        "console": config.console,
        "batch_timeout_ms": config.batch_timeout_ms,
    }


def configure_telemetry(
    config: TelemetryConfig | None = None,
    *,
    otlp_exporter: Any | None = None,
    otlp_processor_factory: Callable[[Any], Any] | None = None,
) -> bool:
    """Apply a telemetry config to the process-wide tracer provider.

    The function returns True when a provider is now active. A second
    call keeps the provider installed by the first call. The optional
    exporter and processor arguments let tests substitute in-memory
    components and avoid network calls.
    """

    config = config or read_telemetry_config()
    if TracerProvider is None or isinstance(trace.get_tracer_provider(), TracerProvider):
        return config.active
    provider = build_tracer_provider(
        config,
        otlp_exporter=otlp_exporter,
        otlp_processor_factory=otlp_processor_factory,
    )
    if provider is None:
        return False
    trace.set_tracer_provider(provider)
    return True


def build_tracer_provider(
    config: TelemetryConfig,
    *,
    otlp_exporter: Any | None = None,
    otlp_processor_factory: Callable[[Any], Any] | None = None,
) -> Any:
    """Build a tracer provider from a telemetry config.

    The function returns None when the config disables every exporter.
    A console exporter prints spans. An OTLP exporter sends spans to a
    local collector over HTTP.
    """

    if not config.active or TracerProvider is None:
        return None
    provider = TracerProvider(resource=Resource.create({"service.name": config.service_name}))
    if config.console:
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    if config.endpoint is not None:
        exporter = otlp_exporter if otlp_exporter is not None else _otlp_exporter(config.endpoint)
        factory = otlp_processor_factory or BatchSpanProcessor
        provider.add_span_processor(factory(exporter))
    return provider


def _otlp_exporter(endpoint: str) -> Any:
    """Build an OTLP HTTP span exporter for a collector endpoint.

    The collector accepts the standard /v1/traces path. The helper adds
    it when the configured endpoint does not include a path.
    """

    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    return OTLPSpanExporter(endpoint=_traces_path(endpoint))


def _traces_path(endpoint: str) -> str:
    normalized = endpoint.rstrip("/")
    if normalized.endswith(_OTLP_TRACES_PATH):
        return normalized
    return f"{normalized}/{_OTLP_TRACES_PATH}"


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        raise ValueError(f"{name} must be an integer") from None


@contextmanager
def traced_operation(name: str, attributes: dict[str, Any] | None = None) -> Iterator[None]:
    """Create a local span around a workbench operation.

    The tracer resolves from the current provider on every call. This
    keeps spans flowing to a provider installed after this module loads.
    """

    tracer = trace.get_tracer(SERVICE_NAME, "0.8.0")
    with tracer.start_as_current_span(
        name,
        kind=SpanKind.INTERNAL,
        attributes={key: str(value) for key, value in (attributes or {}).items()},
    ) as span:
        try:
            yield
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            raise
