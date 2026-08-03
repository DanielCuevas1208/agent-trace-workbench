from fastapi.testclient import TestClient

from agent_trace_workbench.main import create_app
from agent_trace_workbench.storage import TraceStore


def _seed(client, baseline, candidate) -> None:
    client.post("/api/traces", json=baseline.as_jsonable())
    client.post("/api/traces", json=candidate.as_jsonable())


def test_get_run_without_filter_keeps_all_spans(tmp_path, baseline):
    store = TraceStore(tmp_path / "filter.db")
    store.ingest(baseline, "run_baseline.json")

    run = store.get_run("run-baseline-001")

    assert len(run["spans"]) == 4
    assert run["tool_count"] == 2


def test_get_run_filters_by_kind(tmp_path, baseline):
    store = TraceStore(tmp_path / "filter.db")
    store.ingest(baseline, "run_baseline.json")

    run = store.get_run("run-baseline-001", span_kind="tool")

    assert [span["name"] for span in run["spans"]] == [
        "search_catalog",
        "get_inventory",
    ]
    assert run["tool_count"] == 2


def test_get_run_filters_by_status(tmp_path, candidate):
    store = TraceStore(tmp_path / "filter.db")
    store.ingest(candidate, "run_candidate.json")

    run = store.get_run("run-candidate-001", span_status="error")

    assert [span["name"] for span in run["spans"]] == ["agent.run", "reserve_inventory"]
    assert run["tool_count"] == 3


def test_get_run_combines_filters(tmp_path, candidate):
    store = TraceStore(tmp_path / "filter.db")
    store.ingest(candidate, "run_candidate.json")

    run = store.get_run("run-candidate-001", span_kind="tool", span_status="error")

    assert [span["name"] for span in run["spans"]] == ["reserve_inventory"]
    assert run["tool_count"] == 3


def test_get_run_filters_by_tool(tmp_path, candidate):
    store = TraceStore(tmp_path / "filter.db")
    store.ingest(candidate, "run_candidate.json")

    run = store.get_run("run-candidate-001", span_tool="reserve_inventory")

    assert len(run["spans"]) == 1
    assert run["spans"][0]["tool_call"]["name"] == "reserve_inventory"


def test_api_run_filters_spans(tmp_path, baseline, candidate):
    client = TestClient(create_app(tmp_path / "api.db"))
    _seed(client, baseline, candidate)

    response = client.get(
        "/api/runs/run-candidate-001",
        params={"kind": "tool", "status": "error"},
    )

    assert response.status_code == 200
    body = response.json()
    assert [span["name"] for span in body["spans"]] == ["reserve_inventory"]
    assert body["tool_count"] == 3


def test_api_run_unknown_filter_returns_empty_spans(tmp_path, baseline):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())

    response = client.get("/api/runs/run-baseline-001", params={"tool": "nope"})

    assert response.status_code == 200
    assert response.json()["spans"] == []


def test_run_page_renders_filter_controls(tmp_path, baseline):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())

    page = client.get("/runs/run-baseline-001", params={"kind": "tool"})

    assert page.status_code == 200
    assert 'name="kind"' in page.text
    assert "search_catalog" in page.text
    assert "agent.run" not in page.text
