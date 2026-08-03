"""Send recorded workbench runs to a local OpenTelemetry collector.

The workbench keeps trace data on the machine. Point it at a local
collector and it posts the OTLP JSON encoding of a run to the
collector's v1/traces endpoint. This makes a run visible in Jaeger,
Tempo, or the OpenTelemetry Collector without a hosted service.

The export posts over plain HTTP with a short timeout. It retries a
failed request a few times and returns a report with the outcome.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .models import TraceDocument
from .otlp import trace_to_otlp_json
from .telemetry import traced_operation, traces_url

_COLLECTOR_HEADERS = {"Content-Type": "application/json"}
_RETRY_ATTEMPTS = 3
_RETRY_DELAY_SECONDS = 0.05


@dataclass(frozen=True)
class CollectorExportReport:
    """The result of sending one run to a local collector."""

    run_id: str
    status: str
    span_count: int
    endpoint: str
    detail: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible report record."""

        return {
            "run_id": self.run_id,
            "status": self.status,
            "span_count": self.span_count,
            "endpoint": self.endpoint,
            "detail": self.detail,
        }


def export_run_to_collector(
    trace: TraceDocument,
    endpoint: str,
    *,
    timeout_seconds: float = 5.0,
) -> CollectorExportReport:
    """Post one run's OTLP JSON encoding to a collector endpoint."""

    with traced_operation(
        "collector.export",
        {"run.id": trace.run_id, "collector.endpoint": endpoint},
    ):
        payload = trace_to_otlp_json(trace)
        span_count = _span_count(payload)
        body = json.dumps(payload).encode("utf-8")
        detail = _post(traces_url(endpoint), body, timeout_seconds)
        if detail is None:
            return CollectorExportReport(trace.run_id, "accepted", span_count, endpoint)
        return CollectorExportReport(trace.run_id, "failed", span_count, endpoint, detail)


def _post(target: str, body: bytes, timeout_seconds: float) -> str | None:
    """Post JSON to a collector and return None on success or an error message."""

    last_detail: str | None = None
    for attempt in range(_RETRY_ATTEMPTS):
        request = urllib.request.Request(
            target, data=body, headers=_COLLECTOR_HEADERS, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                if 200 <= response.status < 300:
                    return None
                last_detail = f"Collector returned HTTP {response.status}"
        except urllib.error.HTTPError as error:
            last_detail = f"Collector returned HTTP {error.code}"
        except urllib.error.URLError as error:
            last_detail = _url_error_detail(error)
        except (OSError, ValueError) as error:
            last_detail = str(error)
        if attempt < _RETRY_ATTEMPTS - 1:
            time.sleep(_RETRY_DELAY_SECONDS)
    return last_detail


def _url_error_detail(error: urllib.error.URLError) -> str:
    reason = error.reason
    if isinstance(reason, ConnectionRefusedError):
        return "Connection refused"
    return str(reason or error)


def _span_count(payload: dict[str, Any]) -> int:
    return sum(
        len(scope.get("spans", []))
        for group in payload.get("resourceSpans", [])
        for scope in group.get("scopeSpans", [])
    )
