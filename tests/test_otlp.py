import json

import pytest

from agent_trace_workbench.otlp import parse_otlp_json, trace_to_otlp_json

EXTERNAL_OTLP = {
    "resourceSpans": [
        {
            "resource": {
                "attributes": [
                    {"key": "service.name", "value": {"stringValue": "order-service"}},
                    {"key": "service.version", "value": {"stringValue": "1.2.0"}},
                    {"key": "deployment.environment", "value": {"stringValue": "local"}},
                ]
            },
            "scopeSpans": [
                {
                    "scope": {"name": "order-lib", "version": "0.1.0"},
                    "spans": [
                        {
                            "traceId": "5b8efff798038103d269b633813fc60c",
                            "spanId": "7f0a1b2c3d4e5f60",
                            "name": "validate_order",
                            "kind": 2,
                            "startTimeUnixNano": "1544712660000000000",
                            "endTimeUnixNano": "1544712660587612160",
                            "attributes": [
                                {"key": "order.id", "value": {"intValue": "42"}},
                                {"key": "priority", "value": {"stringValue": "high"}},
                                {"key": "in_stock", "value": {"boolValue": True}},
                                {"key": "score", "value": {"doubleValue": 9.5}},
                                {
                                    "key": "tags",
                                    "value": {
                                        "arrayValue": {
                                            "values": [
                                                {"stringValue": "a"},
                                                {"stringValue": "b"},
                                            ]
                                        }
                                    },
                                },
                                {
                                    "key": "meta",
                                    "value": {
                                        "kvlistValue": {
                                            "values": [
                                                {"key": "origin", "value": {"stringValue": "web"}}
                                            ]
                                        }
                                    },
                                },
                            ],
                            "status": {"code": 2, "message": "validation failed"},
                        }
                    ],
                }
            ],
        }
    ]
}


def test_export_produces_otlp_json_shape(baseline):
    payload = trace_to_otlp_json(baseline)

    resource_spans = payload["resourceSpans"]
    assert len(resource_spans) == 1
    resource = resource_spans[0]["resource"]["attributes"]
    by_key = {item["key"]: item["value"] for item in resource}
    assert by_key["service.name"]["stringValue"] == "catalog-assistant"
    assert by_key["workbench.run_id"]["stringValue"] == "run-baseline-001"

    spans = resource_spans[0]["scopeSpans"][0]["spans"]
    assert len(spans) == 4
    first = spans[0]
    assert first["name"] == "agent.run"
    assert first["startTimeUnixNano"] == str(
        int(baseline.spans[0].start_time.timestamp() * 1_000_000_000)
    )
    assert first["status"] == {"code": 1}


def test_round_trip_preserves_workbench_run(baseline):
    restored = parse_otlp_json(trace_to_otlp_json(baseline))

    assert len(restored) == 1
    assert restored[0].model_dump(mode="json") == baseline.model_dump(mode="json")


def test_round_trip_preserves_failed_tool_run(candidate):
    restored = parse_otlp_json(trace_to_otlp_json(candidate))[0]

    assert restored.status == "error"
    tool = restored.tool_spans()[-1]
    assert tool.tool_call is not None
    assert tool.tool_call.name == "reserve_inventory"
    assert tool.tool_call.outcome == "failure"
    assert tool.tool_call.error == "reservation window expired"


def test_round_trip_preserves_nested_attributes(baseline):
    source = baseline.model_copy(deep=True)
    source.spans[1].attributes["note"] = {"depth": 3}
    payload = trace_to_otlp_json(source)

    restored = parse_otlp_json(payload)[0]

    assert restored.spans[1].attributes["note"] == {"depth": 3}


def test_events_and_links_round_trip(baseline):
    payload = trace_to_otlp_json(baseline)
    span_payload = payload["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    span_payload["events"] = [
        {
            "name": "tool.started",
            "timeUnixNano": span_payload["startTimeUnixNano"],
            "attributes": [{"key": "attempt", "value": {"intValue": "1"}}],
        }
    ]
    span_payload["links"] = [
        {
            "traceId": span_payload["traceId"],
            "spanId": "9f0a1b2c3d4e5f60",
            "attributes": [{"key": "related", "value": {"boolValue": True}}],
        }
    ]

    imported = parse_otlp_json(payload)[0]
    attributes = imported.spans[0].attributes
    assert attributes["otlp.events"] == [
        {
            "name": "tool.started",
            "timeUnixNano": span_payload["startTimeUnixNano"],
            "attributes": {"attempt": 1},
        }
    ]
    assert attributes["otlp.links"][0]["attributes"] == {"related": True}

    re_exported = trace_to_otlp_json(imported)
    span_out = re_exported["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    assert span_out["events"][0]["name"] == "tool.started"
    assert span_out["links"][0]["spanId"] == "9f0a1b2c3d4e5f60"
    assert "otlp.events" not in span_out["attributes"]


def test_import_accepts_dict_and_string():
    text = json.dumps(EXTERNAL_OTLP)

    from_dict = parse_otlp_json(EXTERNAL_OTLP)
    from_text = parse_otlp_json(text)

    assert len(from_dict) == len(from_text) == 1
    assert from_dict[0].model_dump(mode="json") == from_text[0].model_dump(mode="json")


def test_import_maps_external_otlp_document():
    documents = parse_otlp_json(EXTERNAL_OTLP)
    assert len(documents) == 1
    trace = documents[0]

    assert trace.agent_name == "order-service"
    assert trace.agent_version == "1.2.0"
    assert trace.trace_id == "5b8efff798038103d269b633813fc60c"
    assert trace.run_id == "order-service-5b8efff79803"
    assert trace.metadata == {
        "otlp_resource": {
            "deployment.environment": "local",
            "service.name": "order-service",
            "service.version": "1.2.0",
        }
    }

    span = trace.spans[0]
    assert span.span_id == "7f0a1b2c3d4e5f60"
    assert span.name == "validate_order"
    assert span.kind == "internal"
    assert span.status == "error"
    assert span.parent_span_id is None
    assert span.attributes == {
        "order.id": 42,
        "priority": "high",
        "in_stock": True,
        "score": 9.5,
        "tags": ["a", "b"],
        "meta": {"origin": "web"},
    }
    assert span.start_time.isoformat() == "2018-12-13T14:51:00+00:00"
    assert span.end_time.isoformat() == "2018-12-13T14:51:00.587612+00:00"


def test_import_infers_run_id_from_service_and_trace():
    documents = parse_otlp_json(EXTERNAL_OTLP)

    assert documents[0].run_id == "order-service-5b8efff79803"


def test_import_maps_otlp_status_codes(baseline):
    payload = trace_to_otlp_json(baseline)
    span_payload = payload["resourceSpans"][0]["scopeSpans"][0]["spans"][0]

    span_payload["status"] = {"code": 0}
    assert parse_otlp_json(payload)[0].spans[0].status == "unset"

    span_payload["status"] = {"code": 2}
    assert parse_otlp_json(payload)[0].spans[0].status == "error"


def test_import_rejects_non_object_payload():
    with pytest.raises(ValueError):
        parse_otlp_json("not json")
    with pytest.raises(ValueError):
        parse_otlp_json({"unexpected": True})
    with pytest.raises(ValueError):
        parse_otlp_json([])


def test_import_rejects_missing_timestamp():
    broken = json.loads(json.dumps(EXTERNAL_OTLP))
    span = broken["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    del span["endTimeUnixNano"]

    with pytest.raises(ValueError):
        parse_otlp_json(broken)


def test_import_skips_empty_resource_group():
    payload = {"resourceSpans": [{"resource": {"attributes": []}}]}

    assert parse_otlp_json(payload) == []


def test_import_accepts_integer_nanoseconds(baseline):
    payload = trace_to_otlp_json(baseline)
    span_payload = payload["resourceSpans"][0]["scopeSpans"][0]["spans"][0]

    span_payload["startTimeUnixNano"] = int(span_payload["startTimeUnixNano"])

    assert parse_otlp_json(payload)[0].spans[0].name == "agent.run"
