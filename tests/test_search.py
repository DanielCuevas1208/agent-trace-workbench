from pathlib import Path

from fastapi.testclient import TestClient

from agent_trace_workbench.main import create_app
from agent_trace_workbench.storage import TraceStore

FIXTURES = str(Path(__file__).parents[1] / "fixtures")


def _seed(store: TraceStore, baseline, candidate) -> None:
    store.ingest(baseline, "run_baseline.json")
    store.ingest(candidate, "run_candidate.json")


def test_search_matches_agent_name(tmp_path, baseline, candidate):
    store = TraceStore(tmp_path / "search.db")
    _seed(store, baseline, candidate)

    results = store.search_runs("catalog-assistant")

    assert {run["run_id"] for run in results} == {
        "run-baseline-001",
        "run-candidate-001",
    }


def test_search_matches_run_id_and_tool_name(tmp_path, baseline, candidate):
    store = TraceStore(tmp_path / "search.db")
    _seed(store, baseline, candidate)

    by_id = store.search_runs("baseline")
    by_tool = store.search_runs("get_inventory")

    assert [run["run_id"] for run in by_id] == ["run-baseline-001"]
    assert {run["run_id"] for run in by_tool} == {
        "run-baseline-001",
        "run-candidate-001",
    }


def test_search_returns_empty_for_no_match(tmp_path, baseline, candidate):
    store = TraceStore(tmp_path / "search.db")
    _seed(store, baseline, candidate)

    assert store.search_runs("does-not-exist") == []


def test_search_escapes_like_wildcards(tmp_path, baseline):
    store = TraceStore(tmp_path / "search.db")
    store.ingest(baseline, "run_baseline.json")

    assert store.search_runs("100%") == []
    assert store.search_runs("baseline")[0]["run_id"] == "run-baseline-001"


def test_search_respects_limit(tmp_path, baseline, candidate):
    store = TraceStore(tmp_path / "search.db")
    _seed(store, baseline, candidate)

    results = store.search_runs("catalog-assistant", limit=1)

    assert len(results) == 1


def test_api_search_returns_matching_runs(tmp_path, baseline, candidate):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())
    client.post("/api/traces", json=candidate.as_jsonable())

    response = client.get("/api/runs", params={"q": "get_inventory"})

    assert response.status_code == 200
    assert [run["run_id"] for run in response.json()] == [
        "run-candidate-001",
        "run-baseline-001",
    ]


def test_api_search_without_query_lists_recent(tmp_path, baseline):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())

    response = client.get("/api/runs")

    assert response.status_code == 200
    assert response.json()[0]["run_id"] == "run-baseline-001"


def test_dashboard_renders_search_box_and_results(tmp_path, baseline, candidate):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())
    client.post("/api/traces", json=candidate.as_jsonable())

    page = client.get("/", params={"q": "reserve_inventory"})

    assert page.status_code == 200
    assert "run-candidate-001" in page.text
    assert "run-baseline-001" not in page.text
