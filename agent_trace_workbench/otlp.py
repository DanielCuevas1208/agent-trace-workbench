"""OTLP JSON import and export for portable trace files.

This module reads the JSON encoding of an OpenTelemetry
ExportTraceServiceRequest and converts it into the workbench trace
contract. It also writes a workbench run back to that same OTLP JSON
shape. The conversion keeps the two directions lossless.

The workbench stores its own fields as span and resource attributes
with a stable "workbench.*" prefix. Files produced by other
OpenTelemetry exporters still import cleanly. Their spans become
internal spans with their recorded attributes preserved.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

from . import __version__
from .models import (
    SpanKind,
    SpanStatus,
    ToolCall,
    ToolOutcome,
    TraceDocument,
    TraceSpan,
)
from .telemetry import traced_operation

_OTLP_KIND_INTERNAL = 1
_OTLP_KIND_CLIENT = 3

_KINDS = set(SpanKind.__args__)
_STATUSES = set(SpanStatus.__args__)
_OUTCOMES = set(ToolOutcome.__args__)

_TRACE_ID_PATTERN = re.compile(r"[0-9a-fA-F]{32}")
_SPAN_ID_PATTERN = re.compile(r"[0-9a-fA-F]{16}")


def parse_otlp_json(payload: str | bytes | dict[str, Any]) -> list[TraceDocument]:
    """Parse an OTLP JSON payload into one workbench run per resource span group."""

    data = _decode_payload(payload)
    resource_spans = data.get("resourceSpans")
    if not isinstance(resource_spans, list):
        raise ValueError("OTLP payload must contain a resourceSpans array")
    documents: list[TraceDocument] = []
    with traced_operation("otlp.import", {"otlp.resource_spans": len(resource_spans)}):
        for entry in resource_spans:
            document = _resource_spans_to_document(entry)
            if document is not None:
                documents.append(document)
    return documents


def trace_to_otlp_json(trace: TraceDocument) -> dict[str, Any]:
    """Convert one workbench run into an OTLP JSON payload."""

    with traced_operation("otlp.export", {"run.id": trace.run_id}):
        spans = [_span_to_otlp(trace, span) for span in trace.ordered_spans()]
        resource = _attribute_list(
            {
                "service.name": trace.agent_name,
                "service.version": trace.agent_version,
                "workbench.run_id": trace.run_id,
                "workbench.trace_id": trace.trace_id,
            }
        )
        if trace.metadata:
            resource.append(_string_attribute("workbench.metadata", _json_string(trace.metadata)))
        return {
            "resourceSpans": [
                {
                    "resource": {"attributes": resource},
                    "scopeSpans": [
                        {
                            "scope": {
                                "name": "agent-trace-workbench",
                                "version": __version__,
                            },
                            "spans": spans,
                        }
                    ],
                }
            ]
        }


def _decode_payload(payload: str | bytes | dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    if not isinstance(payload, (str, bytes)):
        raise ValueError("OTLP payload must be a JSON object or text")
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid OTLP JSON: {error.msg}") from error
    if not isinstance(data, dict):
        raise ValueError("OTLP payload must be a JSON object")
    return data


def _resource_spans_to_document(entry: Any) -> TraceDocument | None:
    if not isinstance(entry, dict):
        raise ValueError("Each resourceSpans entry must be an object")
    resource = entry.get("resource")
    resource_attributes = (
        _attributes_to_dict(resource.get("attributes"))
        if isinstance(resource, dict)
        else {}
    )
    raw_spans = _collect_spans(entry.get("scopeSpans"))
    if not raw_spans:
        return None
    spans = [_span_to_trace_span(span) for span in raw_spans]
    return TraceDocument(
        trace_id=resource_attributes.get("workbench.trace_id") or _derive_trace_id(raw_spans),
        run_id=resource_attributes.get("workbench.run_id")
        or _derive_run_id(resource_attributes, raw_spans),
        agent_name=str(resource_attributes.get("service.name") or "unknown-agent"),
        agent_version=str(resource_attributes.get("service.version") or "local"),
        metadata=_import_metadata(resource_attributes),
        spans=spans,
    )


def _collect_spans(scope_spans: Any) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    if not isinstance(scope_spans, list):
        return spans
    for group in scope_spans:
        if not isinstance(group, dict):
            continue
        group_spans = group.get("spans")
        if isinstance(group_spans, list):
            for span in group_spans:
                if isinstance(span, dict):
                    spans.append(span)
    return spans


def _span_to_trace_span(span: dict[str, Any]) -> TraceSpan:
    attributes = _attributes_to_dict(span.get("attributes"))
    workbench = {
        key: value for key, value in attributes.items() if key.startswith("workbench.")
    }
    user_attributes = {
        key: value for key, value in attributes.items() if not key.startswith("workbench.")
    }
    if "events" in span:
        user_attributes["otlp.events"] = _events_to_list(span["events"])
    if "links" in span:
        user_attributes["otlp.links"] = _links_to_list(span["links"])
    if "traceState" in span and isinstance(span["traceState"], str):
        user_attributes["otlp.trace_state"] = span["traceState"]

    tool_call = _tool_call_from_workbench(workbench)
    kind = _normalized_kind(workbench.get("workbench.kind"), tool_call)
    status = _normalized_status(workbench.get("workbench.status"), span)
    return TraceSpan(
        span_id=workbench.get("workbench.span_id") or str(span.get("spanId") or ""),
        name=str(span.get("name") or ""),
        kind=kind,
        parent_span_id=workbench.get("workbench.parent_span_id")
        or span.get("parentSpanId")
        or None,
        start_time=_nanos_to_datetime(span.get("startTimeUnixNano")),
        end_time=_nanos_to_datetime(span.get("endTimeUnixNano")),
        status=status,
        attributes=user_attributes,
        tool_call=tool_call,
        sequence=_normalized_sequence(workbench.get("workbench.sequence")),
    )


def _tool_call_from_workbench(workbench: dict[str, Any]) -> ToolCall | None:
    name = workbench.get("workbench.tool_call.name")
    if not name:
        return None
    outcome = workbench.get("workbench.tool_call.outcome")
    return ToolCall(
        name=str(name),
        arguments=_json_value(workbench.get("workbench.tool_call.arguments")) or {},
        result=_json_value(workbench.get("workbench.tool_call.result")),
        outcome=outcome if outcome in _OUTCOMES else "unknown",
        error=workbench.get("workbench.tool_call.error"),
    )


def _normalized_kind(value: Any, tool_call: ToolCall | None) -> SpanKind:
    if value in _KINDS:
        if value == "tool" and tool_call is None:
            return "internal"
        return value
    return "internal"


def _normalized_status(value: Any, span: dict[str, Any]) -> SpanStatus:
    if value in _STATUSES:
        return value
    code = span.get("status", {}).get("code") if isinstance(span.get("status"), dict) else None
    if code == 2:
        return "error"
    if code == 1:
        return "ok"
    return "unset"


def _normalized_sequence(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _span_to_otlp(trace: TraceDocument, span: TraceSpan) -> dict[str, Any]:
    attributes = dict(span.attributes)
    attributes["workbench.span_id"] = span.span_id
    attributes["workbench.kind"] = span.kind
    if span.sequence is not None:
        attributes["workbench.sequence"] = span.sequence
    if span.parent_span_id is not None:
        attributes["workbench.parent_span_id"] = span.parent_span_id
    if span.status != "ok":
        attributes["workbench.status"] = span.status
    if span.tool_call is not None:
        call = span.tool_call
        attributes["workbench.tool_call.name"] = call.name
        attributes["workbench.tool_call.arguments"] = _json_string(call.arguments)
        attributes["workbench.tool_call.result"] = _json_string(call.result)
        attributes["workbench.tool_call.outcome"] = call.outcome
        if call.error is not None:
            attributes["workbench.tool_call.error"] = call.error

    events = attributes.pop("otlp.events", None)
    links = attributes.pop("otlp.links", None)
    trace_state = attributes.pop("otlp.trace_state", None)

    payload: dict[str, Any] = {
        "traceId": _trace_id_hex(trace.trace_id),
        "spanId": _span_id_hex(span.span_id),
        "name": span.name,
        "kind": _OTLP_KIND_CLIENT if span.kind == "tool" else _OTLP_KIND_INTERNAL,
        "startTimeUnixNano": str(_datetime_to_nanos(span.start_time)),
        "endTimeUnixNano": str(_datetime_to_nanos(span.end_time)),
        "attributes": _attribute_list(attributes),
        "status": {"code": 1 if span.status == "ok" else 2},
    }
    if span.parent_span_id is not None:
        payload["parentSpanId"] = _span_id_hex(span.parent_span_id)
    if events is not None:
        payload["events"] = events
    if links is not None:
        payload["links"] = links
    if trace_state is not None:
        payload["traceState"] = trace_state
    return payload


def _derive_trace_id(spans: list[dict[str, Any]]) -> str:
    for span in spans:
        trace_id = span.get("traceId")
        if isinstance(trace_id, str) and trace_id:
            return trace_id.lower()
    return hashlib.sha256("empty-trace".encode("utf-8")).hexdigest()[:32]


def _derive_run_id(
    resource_attributes: dict[str, Any],
    spans: list[dict[str, Any]],
) -> str:
    service = str(resource_attributes.get("service.name") or "agent")
    slug = re.sub(r"[^0-9a-zA-Z]+", "-", service).strip("-").lower() or "agent"
    trace_id = _derive_trace_id(spans)
    return f"{slug}-{trace_id[:12]}"


def _import_metadata(resource_attributes: dict[str, Any]) -> dict[str, Any]:
    metadata = _json_value(resource_attributes.get("workbench.metadata"))
    if isinstance(metadata, dict):
        return metadata
    resource = {
        key: value
        for key, value in resource_attributes.items()
        if not key.startswith("workbench.")
    }
    return {"otlp_resource": resource} if resource else {}


def _nanos_to_datetime(value: Any) -> datetime:
    if value is None:
        raise ValueError("OTLP span requires startTimeUnixNano and endTimeUnixNano")
    try:
        nanos = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"Invalid Unix nano timestamp: {value!r}") from error
    seconds, micros = divmod(nanos, 1_000_000_000)
    micros = micros // 1000
    return datetime.fromtimestamp(seconds, tz=timezone.utc).replace(microsecond=micros)


def _datetime_to_nanos(value: datetime) -> int:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    value = value.astimezone(timezone.utc)
    return int(value.timestamp()) * 1_000_000_000 + value.microsecond * 1000


def _attributes_to_dict(attributes: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if not isinstance(attributes, list):
        return result
    for item in attributes:
        if not isinstance(item, dict) or "key" not in item:
            continue
        result[str(item["key"])] = _attribute_value(item.get("value"))
    return result


def _attribute_value(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    for key in ("stringValue", "boolValue", "doubleValue"):
        if key in value:
            return value[key]
    if "intValue" in value:
        try:
            return int(value["intValue"])
        except (TypeError, ValueError):
            return value["intValue"]
    array = value.get("arrayValue")
    if isinstance(array, dict) and isinstance(array.get("values"), list):
        return [_attribute_value(item) for item in array["values"]]
    kvlist = value.get("kvlistValue")
    if isinstance(kvlist, dict) and isinstance(kvlist.get("values"), list):
        return _attributes_to_dict(kvlist["values"])
    return value


def _events_to_list(events: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if not isinstance(events, list):
        return result
    for event in events:
        if not isinstance(event, dict):
            continue
        item: dict[str, Any] = {"name": event.get("name")}
        if "timeUnixNano" in event:
            item["timeUnixNano"] = event["timeUnixNano"]
        if "attributes" in event:
            item["attributes"] = _attributes_to_dict(event["attributes"])
        result.append(item)
    return result


def _links_to_list(links: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if not isinstance(links, list):
        return result
    for link in links:
        if not isinstance(link, dict):
            continue
        item: dict[str, Any] = {}
        if "traceId" in link:
            item["traceId"] = link["traceId"]
        if "spanId" in link:
            item["spanId"] = link["spanId"]
        if "attributes" in link:
            item["attributes"] = _attributes_to_dict(link["attributes"])
        result.append(item)
    return result


def _attribute_list(values: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"key": str(key), "value": _encode_value(value)}
        for key, value in values.items()
    ]


def _encode_value(value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, dict):
        return {
            "kvlistValue": {
                "values": [
                    {"key": str(key), "value": _encode_value(item)}
                    for key, item in value.items()
                ]
            }
        }
    if isinstance(value, list):
        return {"arrayValue": {"values": [_encode_value(item) for item in value]}}
    return {"stringValue": str(value)}


def _string_attribute(key: str, value: str) -> dict[str, Any]:
    return {"key": key, "value": {"stringValue": value}}


def _json_string(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _json_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _trace_id_hex(value: str) -> str:
    if _TRACE_ID_PATTERN.fullmatch(value):
        return value.lower()
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def _span_id_hex(value: str) -> str:
    if _SPAN_ID_PATTERN.fullmatch(value):
        return value.lower()
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
