"""Small OpenTelemetry integration for local workbench operations.

The workbench creates spans for its own operations. It keeps them local
by default. Set ATW_OTEL_CONSOLE=1 to print the spans, or set
ATW_OTEL_COLLECTOR_ENDPOINT to post them to a local OpenTelemetry
collector over the OTLP HTTP JSON encoding.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import urllib.request
from contextlib import contextmanager
from typing import Any, Iterator, Sequence

from . import __version__

try:
    from opentelemetry import trace
    from opentelemetry.sdk.trace.export import (
        ConsoleSpanExporter,
        SimpleSpanProcessor,
        SpanExporter,
        SpanExportResult,
    )
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

    class _NoopSpanExporter:
        def export(self, *_args: Any, **_kwargs: Any) -> Any:
            return None

        def shutdown(self) -> None:
            return None

    class _NoopSpanExportResult:
        SUCCESS = None
        FAILURE = None

    trace = _NoopTrace()
    SpanKind = _NoopSpanKind()
    Status = _NoopStatus
    StatusCode = _NoopStatusCode()
    SpanExporter = _NoopSpanExporter
    SpanExportResult = _NoopSpanExportResult
    SimpleSpanProcessor = None
    ConsoleSpanExporter = None


SERVICE_NAME = "agent-trace-workbench"

_LOG = logging.getLogger(__name__)
_TRACER = trace.get_tracer(SERVICE_NAME, __version__)

_OTLP_JSON_HEADERS = {"Content-Type": "application/json"}
_TRACES_PATH = "/v1/traces"


def traces_url(endpoint: str) -> str:
    """Return the OTLP HTTP traces path for a collector endpoint."""

    normalized = endpoint.strip()
    if "://" not in normalized:
        normalized = f"http://{normalized}"
    return f"{normalized.rstrip('/')}{_TRACES_PATH}"


def configure_telemetry() -> None:
    """Enable local span export to the console or a local collector."""

    endpoint = os.getenv("ATW_OTEL_COLLECTOR_ENDPOINT")
    console = os.getenv("ATW_OTEL_CONSOLE") == "1"
    if not endpoint and not console:
        return
    if SimpleSpanProcessor is None:  # pragma: no cover - optional SDK is absent
        return
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider

    if isinstance(trace.get_tracer_provider(), TracerProvider):
        return
    provider = TracerProvider(resource=Resource.create({"service.name": SERVICE_NAME}))
    if console:
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    if endpoint:
        provider.add_span_processor(SimpleSpanProcessor(OtlpJsonSpanExporter(endpoint)))
    trace.set_tracer_provider(provider)
    atexit.register(provider.force_flush)


class OtlpJsonSpanExporter(SpanExporter):
    """Post workbench spans as OTLP JSON to a local collector."""

    def __init__(self, endpoint: str, *, timeout_seconds: float = 2.0) -> None:
        self._url = traces_url(endpoint)
        self._timeout_seconds = timeout_seconds

    def export(self, spans: Sequence[Any]) -> SpanExportResult:
        if not spans:
            return SpanExportResult.SUCCESS
        try:
            body = json.dumps(_spans_to_otlp_json(spans)).encode("utf-8")
            request = urllib.request.Request(
                self._url, data=body, headers=_OTLP_JSON_HEADERS, method="POST"
            )
            with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
                if 200 <= response.status < 300:
                    return SpanExportResult.SUCCESS
        except Exception:  # noqa: BLE001 - the SDK reports export failures as data
            _LOG.debug("Collector export failed", exc_info=True)
        return SpanExportResult.FAILURE

    def shutdown(self) -> None:
        return None


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


def _spans_to_otlp_json(spans: Sequence[Any]) -> dict[str, Any]:
    from .otlp import _attribute_list

    payloads = [payload for span in spans if (payload := _span_to_otlp_json(span))]
    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": _attribute_list(
                        {"service.name": SERVICE_NAME, "service.version": __version__}
                    )
                },
                "scopeSpans": [
                    {
                        "scope": {"name": SERVICE_NAME, "version": __version__},
                        "spans": payloads,
                    }
                ],
            }
        ]
    }


def _span_to_otlp_json(span: Any) -> dict[str, Any] | None:
    from .otlp import _attribute_list, _encode_value

    context = span.context
    if context is None:
        return None
    payload: dict[str, Any] = {
        "traceId": format(context.trace_id, "032x"),
        "spanId": format(context.span_id, "016x"),
        "name": span.name,
        "kind": span.kind.value,
        "startTimeUnixNano": str(span.start_time),
        "endTimeUnixNano": str(span.end_time),
        "attributes": [
            {"key": str(key), "value": _encode_value(value)}
            for key, value in span.attributes.items()
        ],
        "status": {"code": span.status.status_code.value},
    }
    if span.parent is not None:
        payload["parentSpanId"] = format(span.parent.span_id, "016x")
    if span.status.description:
        payload["status"]["message"] = span.status.description
    if span.events:
        payload["events"] = [
            {
                "name": event.name,
                "timeUnixNano": str(event.timestamp),
                "attributes": _attribute_list(dict(event.attributes)),
            }
            for event in span.events
        ]
    return payload
