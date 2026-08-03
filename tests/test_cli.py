import json
from pathlib import Path

from agent_trace_workbench.cli import main
from agent_trace_workbench.storage import TraceStore

FIXTURES = Path(__file__).parents[1] / "fixtures"


def _run_replay(tmp_path, run_id, extra, monkeypatch, capsys):
    sys_argv = ["atw", "--db", str(tmp_path / "cli.db"), "replay", run_id, *extra]
    monkeypatch.setattr("sys.argv", sys_argv)
    main()
    return json.loads(capsys.readouterr().out)


def test_cli_replay_guards_local_write_handler(tmp_path, candidate, monkeypatch, capsys):
    store = TraceStore(tmp_path / "cli.db")
    store.ingest(candidate, "candidate.json")

    report = _run_replay(
        tmp_path,
        "run-candidate-001",
        ["--config", str(FIXTURES / "handlers.json")],
        monkeypatch,
        capsys,
    )

    assert report["policy"] == "strict"
    assert report["guarded_steps"] == 1
    assert report["steps"][-1]["mode"] == "guarded"
    assert report["steps"][-1]["side_effect_level"] == "local_write"


def test_cli_replay_uses_script_handlers_for_baseline(tmp_path, baseline, monkeypatch, capsys):
    store = TraceStore(tmp_path / "cli.db")
    store.ingest(baseline, "baseline.json")

    report = _run_replay(
        tmp_path,
        "run-baseline-001",
        ["--config", str(FIXTURES / "handlers.json")],
        monkeypatch,
        capsys,
    )

    assert report["total_steps"] == 2
    assert report["matched_steps"] == 2
    assert [step["mode"] for step in report["steps"]] == ["handler", "handler"]


def test_cli_replay_policy_flag_overrides_config(tmp_path, candidate, monkeypatch, capsys):
    store = TraceStore(tmp_path / "cli.db")
    store.ingest(candidate, "candidate.json")

    report = _run_replay(
        tmp_path,
        "run-candidate-001",
        ["--config", str(FIXTURES / "handlers.json"), "--policy", "local"],
        monkeypatch,
        capsys,
    )

    assert report["policy"] == "local"
    assert report["guarded_steps"] == 0
    assert report["steps"][-1]["mode"] == "handler"
