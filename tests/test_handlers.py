from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_trace_workbench.handlers import (
    HandlerConfig,
    ReplayPolicy,
    SideEffectLevel,
    build_registry,
    load_handler_config,
    side_effect_allowed,
)
from agent_trace_workbench.replay import ReplayEngine, default_replay_engine

FIXTURES = Path(__file__).parents[1] / "fixtures"


def test_side_effect_policy_matrix():
    assert side_effect_allowed(SideEffectLevel.READ_ONLY, ReplayPolicy.STRICT) is True
    assert side_effect_allowed(SideEffectLevel.LOCAL_WRITE, ReplayPolicy.STRICT) is False
    assert side_effect_allowed(SideEffectLevel.NETWORK, ReplayPolicy.STRICT) is False
    assert side_effect_allowed(SideEffectLevel.UNKNOWN, ReplayPolicy.STRICT) is False
    assert side_effect_allowed(SideEffectLevel.LOCAL_WRITE, ReplayPolicy.LOCAL) is True
    assert side_effect_allowed(SideEffectLevel.NETWORK, ReplayPolicy.LOCAL) is False
    assert side_effect_allowed(SideEffectLevel.UNKNOWN, ReplayPolicy.ALL) is True


def test_config_loads_bundled_handlers():
    config = load_handler_config(FIXTURES / "handlers.json")
    assert config.policy == ReplayPolicy.STRICT
    assert [entry.tool for entry in config.handlers] == [
        "search_catalog",
        "get_inventory",
        "reserve_inventory",
    ]


def test_build_registry_resolves_scripts_and_results():
    config = load_handler_config(FIXTURES / "handlers.json")
    registry = build_registry(config, base_dir=FIXTURES)
    assert registry["search_catalog"].func is not None
    assert registry["search_catalog"].side_effect == SideEffectLevel.READ_ONLY
    assert registry["reserve_inventory"].func is None
    assert registry["reserve_inventory"].fixed_result["status"] == "confirmed"


def test_config_rejects_entry_without_behavior():
    with pytest.raises(ValidationError, match="exactly one"):
        HandlerConfig.model_validate(
            {"handlers": [{"tool": "orphan_tool", "side_effect": "read_only"}]}
        )


def test_config_rejects_unknown_side_effect():
    with pytest.raises(ValidationError):
        HandlerConfig.model_validate(
            {"handlers": [{"tool": "x", "result": 1, "side_effect": "writes_family_photos"}]}
        )


def test_build_registry_rejects_duplicate_tools():
    config = HandlerConfig.model_validate(
        {"handlers": [{"tool": "x", "result": 1}, {"tool": "x", "result": 2}]}
    )
    with pytest.raises(ValueError, match="Duplicate"):
        build_registry(config)


def test_missing_script_raises(tmp_path):
    config = HandlerConfig.model_validate(
        {"handlers": [{"tool": "x", "script": "missing.py", "side_effect": "read_only"}]}
    )
    with pytest.raises(FileNotFoundError):
        build_registry(config, base_dir=tmp_path)


def test_script_without_run_function_raises(tmp_path):
    bad_script = tmp_path / "bad.py"
    bad_script.write_text("VALUE = 1\n", encoding="utf-8")
    config = HandlerConfig.model_validate(
        {"handlers": [{"tool": "x", "script": str(bad_script), "side_effect": "read_only"}]}
    )
    with pytest.raises(ValueError, match="run"):
        build_registry(config)


def test_engine_load_config_replays_with_script_handlers(baseline):
    engine = default_replay_engine()
    config = load_handler_config(FIXTURES / "handlers.json")
    engine.load_config(config, base_dir=FIXTURES)

    report = engine.replay(baseline)

    assert report.policy == "strict"
    assert report.total_steps == 2
    assert report.matched_steps == 2
    assert [step.mode for step in report.steps] == ["handler", "handler"]
    assert [step.side_effect_level for step in report.steps] == ["read_only", "read_only"]


def test_guard_blocks_local_write_handler_under_strict(candidate):
    engine = default_replay_engine()
    config = load_handler_config(FIXTURES / "handlers.json")
    engine.load_config(config, base_dir=FIXTURES)

    report = engine.replay(candidate)

    guarded = report.steps[-1]
    assert guarded.tool_name == "reserve_inventory"
    assert guarded.mode == "guarded"
    assert guarded.guarded is True
    assert guarded.side_effect_level == "local_write"
    assert guarded.replayed_outcome == "failure"
    assert guarded.replayed_result is None
    assert guarded.result_match is True
    assert report.guarded_steps == 1
    assert report.failed_steps == 1


def test_local_policy_runs_local_write_handler(candidate):
    engine = default_replay_engine()
    config = load_handler_config(FIXTURES / "handlers.json")
    config.policy = ReplayPolicy.LOCAL
    engine.load_config(config, base_dir=FIXTURES)

    report = engine.replay(candidate)

    reserved = report.steps[-1]
    assert reserved.mode == "handler"
    assert reserved.guarded is False
    assert reserved.replayed_outcome == "success"
    assert reserved.result_match is False
    assert report.guarded_steps == 0
    assert report.failed_steps == 0


def test_inline_handler_with_unknown_level_is_guarded_under_strict(baseline):
    engine = ReplayEngine()
    engine.register("get_inventory", lambda args: {"available": 99})

    report = engine.replay(baseline)

    guarded = report.steps[-1]
    assert guarded.mode == "guarded"
    assert guarded.guarded is True
    assert guarded.replayed_result == {"sku": "lamp-01", "available": 12}
