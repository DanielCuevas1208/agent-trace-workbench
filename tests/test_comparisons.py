import json
from pathlib import Path

from fastapi.testclient import TestClient

from agent_trace_workbench.cli import main
from agent_trace_workbench.main import create_app
from agent_trace_workbench.storage import TraceStore

FIXTURES = str(Path(__file__).parents[1] / "fixtures")


def _seed(client, baseline, candidate) -> None:
    client.post("/api/traces", json=baseline.as_jsonable())
    client.post("/api/traces", json=candidate.as_jsonable())


def test_save_comparison_round_trips_through_api(tmp_path, baseline, candidate):
    client = TestClient(create_app(tmp_path / "api.db"))
    _seed(client, baseline, candidate)

    saved = client.post(
        "/api/comparisons",
        json={"run_a": "run-baseline-001", "run_b": "run-candidate-001", "label": "v1 vs v2"},
    )

    assert saved.status_code == 201
    body = saved.json()
    assert body["label"] == "v1 vs v2"
    assert body["report"]["changed_tools"] == 2
    assert body["report"]["tool_diffs"][0]["state"] == "same"

    listing = client.get("/api/comparisons")
    assert listing.status_code == 200
    assert [item["comparison_id"] for item in listing.json()] == [body["comparison_id"]]

    fetched = client.get(f"/api/comparisons/{body['comparison_id']}")
    assert fetched.status_code == 200
    assert fetched.json()["label"] == "v1 vs v2"


def test_save_comparison_rejects_missing_runs(tmp_path, baseline):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())

    response = client.post(
        "/api/comparisons",
        json={"run_a": "run-baseline-001", "run_b": "missing", "label": "broken"},
    )

    assert response.status_code == 404


def test_delete_comparison(tmp_path, baseline, candidate):
    client = TestClient(create_app(tmp_path / "api.db"))
    _seed(client, baseline, candidate)
    saved = client.post(
        "/api/comparisons",
        json={"run_a": "run-baseline-001", "run_b": "run-candidate-001", "label": "temp"},
    ).json()

    deleted = client.delete(f"/api/comparisons/{saved['comparison_id']}")

    assert deleted.status_code == 200
    assert client.get("/api/comparisons").json() == []


def test_delete_missing_comparison_returns_404(tmp_path, baseline):
    client = TestClient(create_app(tmp_path / "api.db"))
    client.post("/api/traces", json=baseline.as_jsonable())

    assert client.delete("/api/comparisons/nope").status_code == 404


def test_save_comparison_validates_label(tmp_path, baseline, candidate):
    client = TestClient(create_app(tmp_path / "api.db"))
    _seed(client, baseline, candidate)

    response = client.post(
        "/api/comparisons",
        json={"run_a": "run-baseline-001", "run_b": "run-candidate-001", "label": ""},
    )

    assert response.status_code == 422


def test_storage_comparison_crud(tmp_path, baseline, candidate):
    store = TraceStore(tmp_path / "cmp.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(candidate, "candidate.json")

    saved = store.save_comparison(
        "run-baseline-001",
        "run-candidate-001",
        "cli-label",
        {"changed_tools": 2},
    )

    assert saved["report"] == {"changed_tools": 2}
    assert store.get_comparison(saved["comparison_id"])["label"] == "cli-label"
    assert store.delete_comparison(saved["comparison_id"]) is True
    assert store.delete_comparison(saved["comparison_id"]) is False


def test_cli_comparisons_list_and_delete(tmp_path, baseline, candidate, monkeypatch, capsys):
    store = TraceStore(tmp_path / "cli.db")
    store.ingest(baseline, "baseline.json")
    store.ingest(candidate, "candidate.json")
    saved = store.save_comparison(
        "run-baseline-001", "run-candidate-001", "saved-flow", {"changed_tools": 2}
    )

    monkeypatch.setattr("sys.argv", ["atw", "--db", str(tmp_path / "cli.db"), "comparisons"])
    main()
    listed = json.loads(capsys.readouterr().out)
    assert [item["label"] for item in listed] == ["saved-flow"]

    monkeypatch.setattr(
        "sys.argv",
        [
            "atw",
            "--db",
            str(tmp_path / "cli.db"),
            "comparisons",
            "--delete",
            saved["comparison_id"],
        ],
    )
    main()
    deleted = json.loads(capsys.readouterr().out)
    assert deleted["deleted"] == saved["comparison_id"]
    assert store.list_comparisons() == []
