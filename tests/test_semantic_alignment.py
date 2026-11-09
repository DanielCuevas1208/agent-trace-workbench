from __future__ import annotations

from agent_trace_workbench.compare import compare_runs
from agent_trace_workbench.models import TraceDocument


def _trace_with_tools(baseline, tools, *, run_id: str) -> TraceDocument:
    payload = baseline.model_dump(mode="json")
    payload["run_id"] = run_id
    payload["spans"] = [payload["spans"][0], *tools]
    return TraceDocument.model_validate(payload)


def _tool(span_id: str, name: str, sequence: int, *, call_id: str | None = None) -> dict:
    return {
        "span_id": span_id,
        "name": name,
        "kind": "tool",
        "parent_span_id": "span-agent-001",
        "start_time": f"2026-07-31T09:00:00.{sequence + 20:06d}+00:00",
        "end_time": f"2026-07-31T09:00:00.{sequence + 30:06d}+00:00",
        "status": "ok",
        "sequence": sequence,
        "tool_call": {
            "call_id": call_id,
            "name": name,
            "arguments": {"step": sequence},
            "result": {"step": sequence},
            "outcome": "success",
        },
    }


def test_inserted_call_does_not_shift_later_identity_matches(baseline):
    candidate = _trace_with_tools(
        baseline,
        [
            _tool("candidate-search", "search_catalog", 1),
            _tool("candidate-note", "write_note", 2),
            _tool("candidate-inventory", "get_inventory", 3),
        ],
        run_id="inserted-call",
    )

    report = compare_runs(baseline, candidate)

    assert [(diff.tool_a, diff.tool_b, diff.alignment) for diff in report.tool_diffs] == [
        ("search_catalog", "search_catalog", "parent-tool"),
        (None, "write_note", "unmatched"),
        ("get_inventory", "get_inventory", "parent-tool"),
    ]
    assert report.tool_diffs[2].alignment == "parent-tool"
    assert report.tool_diffs[2].ambiguous is False


def test_repeated_tool_names_use_explicit_call_ids(baseline):
    baseline_run = _trace_with_tools(
        baseline,
        [
            _tool("a-1", "lookup", 1, call_id="lookup-1"),
            _tool("a-2", "lookup", 2, call_id="lookup-2"),
        ],
        run_id="repeated-a",
    )
    candidate_run = _trace_with_tools(
        baseline,
        [
            _tool("b-1", "lookup", 1, call_id="lookup-1"),
            _tool("b-new", "lookup", 2, call_id="lookup-new"),
            _tool("b-2", "lookup", 3, call_id="lookup-2"),
        ],
        run_id="repeated-b",
    )

    report = compare_runs(baseline_run, candidate_run)

    assert [(diff.call_id_a, diff.call_id_b, diff.alignment) for diff in report.tool_diffs] == [
        ("lookup-1", "lookup-1", "call-id"),
        (None, "lookup-new", "unmatched"),
        ("lookup-2", "lookup-2", "call-id"),
    ]
    assert report.ambiguous_matches == 0


def test_repeated_names_without_ids_are_marked_ambiguous(baseline):
    baseline_run = _trace_with_tools(
        baseline,
        [_tool("a-1", "lookup", 1), _tool("a-2", "lookup", 2)],
        run_id="missing-id-a",
    )
    candidate_run = _trace_with_tools(
        baseline,
        [_tool("b-1", "lookup", 1), _tool("b-2", "lookup", 2)],
        run_id="missing-id-b",
    )

    report = compare_runs(baseline_run, candidate_run)

    assert report.ambiguous_matches == 2
    assert all(diff.alignment == "parent-tool" for diff in report.tool_diffs)
    assert all(diff.ambiguity_reason is None for diff in report.tool_diffs)
    assert report.tool_diffs[0].candidates_a == ["a-1", "a-2"]
    assert report.tool_diffs[0].candidates_b == ["b-1", "b-2"]


def test_missing_ids_make_position_fallback_visible(baseline):
    baseline_run = _trace_with_tools(
        baseline,
        [_tool("a-1", "lookup", 1)],
        run_id="fallback-a",
    )
    candidate_run = _trace_with_tools(
        baseline,
        [_tool("b-1", "renamed_lookup", 1)],
        run_id="fallback-b",
    )

    diff = compare_runs(baseline_run, candidate_run).tool_diffs[0]

    assert diff.alignment == "position-fallback"
    assert diff.ambiguous is True
    assert diff.ambiguity_reason == "No stable call identity matched; recorded position was used."


def test_parent_call_ids_keep_nested_calls_distinct(baseline):
    parent_a = _tool("parent-a", "delegate", 1, call_id="branch-a")
    parent_b = _tool("parent-b", "delegate", 2, call_id="branch-b")
    child_a = _tool("child-a", "lookup", 3)
    child_a["parent_span_id"] = "parent-a"
    child_a["tool_call"]["parent_call_id"] = "branch-a"
    child_b = _tool("child-b", "lookup", 4)
    child_b["parent_span_id"] = "parent-b"
    child_b["tool_call"]["parent_call_id"] = "branch-b"
    first = _trace_with_tools(baseline, [parent_a, child_a, parent_b, child_b], run_id="nested-a")

    parent_a_b = _tool("new-parent-a", "delegate", 1, call_id="branch-a")
    parent_b_b = _tool("new-parent-b", "delegate", 2, call_id="branch-b")
    child_b_b = _tool("new-child-b", "lookup", 3)
    child_b_b["parent_span_id"] = "new-parent-b"
    child_b_b["tool_call"]["parent_call_id"] = "branch-b"
    child_a_b = _tool("new-child-a", "lookup", 4)
    child_a_b["parent_span_id"] = "new-parent-a"
    child_a_b["tool_call"]["parent_call_id"] = "branch-a"
    second = _trace_with_tools(
        baseline,
        [parent_a_b, child_b_b, parent_b_b, child_a_b],
        run_id="nested-b",
    )

    report = compare_runs(first, second)
    children = [diff for diff in report.tool_diffs if diff.tool_a == "lookup"]

    assert [(diff.parent_span_id_a, diff.parent_span_id_b) for diff in children] == [
        ("parent-a", "new-parent-a"),
        ("parent-b", "new-parent-b"),
    ]
    assert all(diff.alignment == "parent-tool" for diff in children)
    assert all(diff.ambiguous is False for diff in children)
