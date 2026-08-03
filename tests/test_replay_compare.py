from agent_trace_workbench.compare import compare_runs
from agent_trace_workbench.replay import default_replay_engine


def test_default_replay_matches_the_baseline_fixture(baseline):
    report = default_replay_engine().replay(baseline)
    assert report.deterministic is True
    assert report.total_steps == 2
    assert report.matched_steps == 2
    assert [step.mode for step in report.steps] == ["handler", "handler"]


def test_replay_preserves_a_recorded_failure_when_no_handler_exists(candidate):
    report = default_replay_engine().replay(candidate)
    failed = report.steps[-1]
    assert failed.mode == "recorded-fallback"
    assert failed.replayed_outcome == "failure"
    assert failed.result_match is True
    assert report.failed_steps == 1


def test_compare_marks_result_timing_and_added_call_changes(baseline, candidate):
    report = compare_runs(baseline, candidate)
    assert report.changed_tools == 2
    assert report.total_duration_delta_ms == 60.0
    assert report.tool_diffs[0].state == "same"
    assert report.tool_diffs[0].arguments_changed is False
    assert report.tool_diffs[0].duration_delta_ms == 15.0
    assert report.tool_diffs[1].result_changed is True
    assert report.tool_diffs[2].state == "added"


def test_compare_reports_field_level_key_changes(baseline, candidate):
    report = compare_runs(baseline, candidate)

    assert report.added_tools == 1
    assert report.removed_tools == 0
    assert report.outcome_changed_tools == 1
    assert report.error_changed_tools == 1

    assert report.tool_diffs[1].result_keys_changed == ["available"]
    assert report.tool_diffs[1].argument_keys_changed == []
    assert report.tool_diffs[1].error_changed is False

    added = report.tool_diffs[2]
    assert added.error_changed is True
    assert added.error_b == "reservation window expired"
    assert added.argument_keys_changed == []
    assert added.result_keys_changed == []

