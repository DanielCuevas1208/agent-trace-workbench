from pathlib import Path

from fastapi.testclient import TestClient

from agent_trace_workbench.main import create_app

FIXTURES = str(Path(__file__).parents[1] / "fixtures")


def test_api_supports_ingest_inspect_replay_and_compare(tmp_path, baseline, candidate):
    client = TestClient(create_app(tmp_path / "api.db"))
    baseline_response = client.post("/api/traces", json=baseline.as_jsonable())
    candidate_response = client.post("/api/traces", json=candidate.as_jsonable())

    assert baseline_response.status_code == 201
    assert candidate_response.status_code == 201
    assert client.get("/api/runs").json()[0]["tool_count"] == 3
    assert client.get("/api/runs/run-baseline-001").json()["tool_count"] == 2
    replay = client.get("/api/runs/run-baseline-001/replay")
    assert replay.status_code == 200
    assert replay.json()["matched_steps"] == 2
    comparison = client.get(
        "/api/compare", params={"run_a": "run-baseline-001", "run_b": "run-candidate-001"}
    )
    assert comparison.status_code == 200
    assert comparison.json()["changed_tools"] == 2
    assert "catalog-assistant" in client.get("/").text


def test_api_reports_missing_runs(tmp_path):
    client = TestClient(create_app(tmp_path / "api.db"))
    assert client.get("/api/runs/not-here").status_code == 404


def test_api_applies_environment_handler_config(tmp_path, candidate, monkeypatch):
    monkeypatch.setenv("ATW_HANDLERS_CONFIG", FIXTURES + "/handlers.json")
    monkeypatch.setenv("ATW_REPLAY_POLICY", "strict")
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=candidate.as_jsonable())

    report = client.get("/api/runs/run-candidate-001/replay").json()

    assert report["policy"] == "strict"
    assert report["guarded_steps"] == 1
    assert report["steps"][-1]["mode"] == "guarded"


def test_compare_page_shows_richer_views_and_csv_link(tmp_path, baseline, candidate):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())
    client.post("/api/traces", json=candidate.as_jsonable())

    response = client.get(
        "/compare", params={"run_a": "run-baseline-001", "run_b": "run-candidate-001"}
    )

    assert response.status_code == 200
    page = response.text
    assert "Field-level delta" in page
    assert "Outcome changes" in page
    assert "state-filter" in page
    assert "format=csv" in page
    assert "available" in page
