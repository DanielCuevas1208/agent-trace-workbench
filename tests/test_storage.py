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


def test_store_returns_none_for_unknown_run(tmp_path):
    assert TraceStore(tmp_path / "trace.db").get_run("missing") is None
