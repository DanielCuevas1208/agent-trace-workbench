"""Small OpenTelemetry integration for local workbench operations."""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator

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
_TRACER = trace.get_tracer(SERVICE_NAME, "0.6.0")


def configure_telemetry() -> None:
    """Enable an optional local console exporter for workbench operations."""

    if os.getenv("ATW_OTEL_CONSOLE") != "1":
        return

    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor

    if isinstance(trace.get_tracer_provider(), TracerProvider):
        return
    provider = TracerProvider(resource=Resource.create({"service.name": SERVICE_NAME}))
    provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(provider)


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
