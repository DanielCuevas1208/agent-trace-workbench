import json

import pytest
from fastapi.testclient import TestClient

from agent_trace_workbench.cli import main
from agent_trace_workbench.export import review_bundle_to_json
from agent_trace_workbench.main import create_app
from agent_trace_workbench.models import ReviewBundle
from agent_trace_workbench.storage import TraceStore


def _payload(trace) -> dict[str, object]:
    return {
        "format": "agent-trace-workbench.review.v1",
        "trace": trace.as_jsonable(),
        "review": {
            "label": "golden",
            "note": "Reference evidence.",
            "decision": "rejected",
            "decision_at": "2026-09-02T12:00:00+00:00",
            "decision_history": [
                {
                    "history_id": 2,
                    "run_id": trace.run_id,
                    "previous_decision": "accepted",
                    "decision": "rejected",
                    "changed_at": "2026-09-02T12:00:00+00:00",
                },
                {
                    "history_id": 1,
                    "run_id": trace.run_id,
                    "previous_decision": "pending",
                    "decision": "accepted",
                    "changed_at": "2026-09-01T12:00:00+00:00",
                },
            ],
        },
    }


def test_store_restores_trace_review_context_and_history(tmp_path, baseline):
    bundle = ReviewBundle.model_validate(_payload(baseline))
    store = TraceStore(tmp_path / "restore.db")

    restored = store.restore_review_bundle(bundle, "evidence.review.json", source_dir="backups")

    assert restored["run_id"] == baseline.run_id
    assert restored["source_name"] == "evidence.review.json"
    assert restored["source_dir"] == "backups"
    assert restored["label"] == "golden"
    assert restored["note"] == "Reference evidence."
    assert restored["decision"] == "rejected"
    assert restored["decision_at"] == "2026-09-02T12:00:00+00:00"
    assert (
        store.get_trace(baseline.run_id).model_dump(mode="json")
        == baseline.model_dump(mode="json")
    )
    assert [item["decision"] for item in store.decision_history(baseline.run_id)] == [
        "rejected",
        "accepted",
    ]
    assert [item["changed_at"] for item in store.decision_history(baseline.run_id)] == [
        "2026-09-02T12:00:00+00:00",
        "2026-09-01T12:00:00+00:00",
    ]


def test_store_restore_replaces_existing_review_snapshot(tmp_path, baseline):
    store = TraceStore(tmp_path / "replace.db")
    store.ingest(baseline, "original.json")
    store.update_annotations(
        baseline.run_id,
        label="local",
        note="Local note.",
        decision="accepted",
    )

    store.restore_review_bundle(ReviewBundle.model_validate(_payload(baseline)))

    run = store.get_run(baseline.run_id)
    assert run["label"] == "golden"
    assert run["note"] == "Reference evidence."
    assert run["decision"] == "rejected"
    assert len(store.decision_history(baseline.run_id)) == 2


def test_review_bundle_requires_matching_history_run_id(baseline):
    payload = _payload(baseline)
    payload["review"]["decision_history"][0]["run_id"] = "other-run"

    with pytest.raises(ValueError, match="must reference"):
        ReviewBundle.model_validate(payload)


def test_review_bundle_rejects_pending_timestamp(baseline):
    payload = _payload(baseline)
    payload["review"]["decision"] = "pending"
    payload["review"]["decision_at"] = "2026-09-02T12:00:00+00:00"

    with pytest.raises(ValueError, match="pending decisions"):
        ReviewBundle.model_validate(payload)


def test_api_restores_review_bundle(tmp_path, baseline):
    client = TestClient(create_app(tmp_path / "api.db"))

    response = client.post(
        "/api/review-bundles",
        json=_payload(baseline),
        headers={"x-trace-source": "uploaded.review.json"},
    )

    assert response.status_code == 201
    assert response.json()["run_id"] == baseline.run_id
    assert response.json()["label"] == "golden"
    assert response.json()["decision"] == "rejected"
    assert response.json()["source_name"] == "uploaded.review.json"
    assert [item["decision"] for item in client.get(
        f"/api/runs/{baseline.run_id}/decision-history"
    ).json()["history"]] == ["rejected", "accepted"]


def test_api_rejects_unknown_review_bundle_version(tmp_path, baseline):
    payload = _payload(baseline)
    payload["format"] = "agent-trace-workbench.review.v2"

    response = TestClient(create_app(tmp_path / "api.db")).post(
        "/api/review-bundles", json=payload
    )

    assert response.status_code == 422


def test_cli_import_review_restores_bundle(tmp_path, baseline, monkeypatch, capsys):
    source = tmp_path / "run.review.json"
    source.write_text(json.dumps(_payload(baseline)), encoding="utf-8")
    database = tmp_path / "cli.db"

    monkeypatch.setattr(
        "sys.argv",
        ["atw", "--db", str(database), "import-review", str(source)],
    )
    main()

    report = json.loads(capsys.readouterr().out)
    assert report["restored_runs"] == 1
    assert report["runs"][0]["run_id"] == baseline.run_id
    restored = TraceStore(database).get_run(baseline.run_id)
    assert restored["label"] == "golden"
    assert restored["decision"] == "rejected"


def test_cli_restore_review_alias_is_supported(tmp_path, baseline, monkeypatch, capsys):
    source = tmp_path / "alias.review.json"
    source.write_text(json.dumps(_payload(baseline)), encoding="utf-8")

    monkeypatch.setattr(
        "sys.argv",
        ["atw", "--db", str(tmp_path / "alias.db"), "restore-review", str(source)],
    )
    main()

    assert json.loads(capsys.readouterr().out)["restored_runs"] == 1


def test_review_export_payload_round_trips_into_store(tmp_path, baseline):
    source_store = TraceStore(tmp_path / "source.db")
    source_store.ingest(baseline, "baseline.json")
    source_store.update_annotations(
        baseline.run_id,
        label="golden",
        note="Keep",
        decision="accepted",
    )
    payload = review_bundle_to_json(
        baseline,
        source_store.get_run(baseline.run_id),
        source_store.decision_history(baseline.run_id),
    )

    target_store = TraceStore(tmp_path / "target.db")
    target_store.restore_review_bundle(ReviewBundle.model_validate(payload))

    restored = target_store.get_run(baseline.run_id)
    assert restored["label"] == "golden"
    assert restored["note"] == "Keep"
    assert restored["decision"] == "accepted"
    assert len(target_store.decision_history(baseline.run_id)) == 1


def test_dashboard_exposes_review_bundle_restore(tmp_path):
    page = TestClient(create_app(tmp_path / "dashboard.db")).get("/")

    assert page.status_code == 200
    assert "restore-review-form" in page.text
    assert "review-bundle-file" in page.text
    assert "Six paths. One local record." in page.text
    assert "atw import-review path/to/run.review.json" in page.text
