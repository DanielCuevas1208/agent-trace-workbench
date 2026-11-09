from agent_trace_workbench.storage import TraceStore


def test_store_ingest_is_idempotent_and_keeps_tool_calls(tmp_path, baseline):
    store = TraceStore(tmp_path / "trace.db")
    first = store.ingest(baseline, "baseline.json")
    second = store.ingest(baseline, "baseline-again.json")

    assert first["run_id"] == "run-baseline-001"
    assert second["source_name"] == "baseline-again.json"
    assert len(store.list_runs()) == 1
    run = store.get_run("run-baseline-001")
    assert run["tool_count"] == 2
    assert run["spans"][1]["tool_call"]["name"] == "search_catalog"


def test_store_round_trips_call_identity_fields(tmp_path, baseline):
    payload = baseline.model_dump(mode="json")
    payload["spans"][1]["tool_call"]["call_id"] = "search-1"
    payload["spans"][1]["tool_call"]["parent_call_id"] = "agent-1"
    trace = type(baseline).model_validate(payload)
    store = TraceStore(tmp_path / "identity.db")

    store.ingest(trace)

    run = store.get_run(trace.run_id)
    assert run["spans"][1]["tool_call"]["call_id"] == "search-1"
    assert run["spans"][1]["tool_call"]["parent_call_id"] == "agent-1"
    restored = store.get_trace(trace.run_id)
    assert restored is not None
    assert restored.tool_spans()[0].tool_call.call_id == "search-1"


def test_store_returns_none_for_unknown_run(tmp_path):
    assert TraceStore(tmp_path / "trace.db").get_run("missing") is None
