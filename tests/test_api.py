from fastapi.testclient import TestClient

from agent_trace_workbench.main import create_app


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
