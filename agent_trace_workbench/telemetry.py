"""OpenTelemetry integration for local workbench operations.

Release 0.8 adds an OTLP HTTP exporter. The workbench sends its spans to
a local OpenTelemetry collector when ATW_OTEL_OTLP_ENDPOINT (or the
standard OTEL_EXPORTER_OTLP_ENDPOINT) is set. The console exporter stays
available through ATW_OTEL_CONSOLE. Both can run at the same time.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator

from . import __version__

try:
    from opentelemetry import trace
    from opentelemetry.trace import SpanKind, Status, StatusCode
except ImportError:  # pragma: no cover - used only when optional local packages are unavailable
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
_TRACER = trace.get_tracer(SERVICE_NAME, __version__)

_TRACES_PATH = "/v1/traces"


def telemetry_info() -> dict[str, Any]:
    """Return the active local telemetry configuration."""

    endpoint = _otlp_traces_endpoint()
    return {
        "service_name": SERVICE_NAME,
        "version": __version__,
        "console_exporter": os.getenv("ATW_OTEL_CONSOLE") == "1",
        "otlp_exporter": endpoint is not None,
        "otlp_endpoint": endpoint,
    }


def build_span_processors() -> list[Any]:
    """Build the span processors enabled by the current environment."""

    processors: list[Any] = []
    if os.getenv("ATW_OTEL_CONSOLE") == "1":
        from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor

        processors.append(SimpleSpanProcessor(ConsoleSpanExporter()))
    endpoint = _otlp_traces_endpoint()
    if endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        processors.append(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    return processors


def configure_telemetry() -> None:
    """Enable the local span exporters selected by the environment."""

    processors = build_span_processors()
    if not processors:
        return
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider

    if isinstance(trace.get_tracer_provider(), TracerProvider):
        return
    provider = TracerProvider(resource=Resource.create({"service.name": SERVICE_NAME}))
    for processor in processors:
        provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)


def shutdown_telemetry() -> None:
    """Flush and stop the active global span processors."""

    provider = trace.get_tracer_provider()
    if provider is None:
        return
    from opentelemetry.sdk.trace import TracerProvider

    if isinstance(provider, TracerProvider):
        provider.shutdown()


def _otlp_traces_endpoint() -> str | None:
    value = os.getenv("ATW_OTEL_OTLP_ENDPOINT") or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not value:
        return None
    base = value.rstrip("/")
    if base.endswith(_TRACES_PATH):
        return base
    return f"{base}{_TRACES_PATH}"


@contextmanager
def traced_operation(name: str, attributes: dict[str, Any] | None = None) -> Iterator[None]:
    """Create a local span around a workbench operation."""

    with _TRACER.start_as_current_span(
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
