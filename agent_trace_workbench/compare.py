"""Compare tool calls by stable identity, parent context, and recorded values."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .models import TraceDocument, TraceSpan
from .replay import canonical_hash
from .telemetry import traced_operation


@dataclass(frozen=True)
class ToolDiff:
    """Comparison result for one aligned tool call."""

    index: int
    tool_a: str | None
    tool_b: str | None
    state: str
    arguments_changed: bool
    outcome_changed: bool
    result_changed: bool
    duration_delta_ms: float | None
    error_a: str | None
    error_b: str | None
    argument_keys_changed: list[str] = field(default_factory=list)
    result_keys_changed: list[str] = field(default_factory=list)
    error_changed: bool = False
    span_id_a: str | None = None
    span_id_b: str | None = None
    call_id_a: str | None = None
    call_id_b: str | None = None
    parent_span_id_a: str | None = None
    parent_span_id_b: str | None = None
    parent_changed: bool = False
    alignment: str = "unmatched"
    ambiguous: bool = False
    ambiguity_reason: str | None = None
    candidates_a: list[str] = field(default_factory=list)
    candidates_b: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class CompareReport:
    """Comparison summary for two trace documents."""

    run_a: dict[str, Any]
    run_b: dict[str, Any]
    tool_diffs: list[ToolDiff]

    @property
    def changed_tools(self) -> int:
        return sum(diff.state != "same" for diff in self.tool_diffs)

    @property
    def added_tools(self) -> int:
        return sum(diff.state == "added" for diff in self.tool_diffs)

    @property
    def removed_tools(self) -> int:
        return sum(diff.state == "removed" for diff in self.tool_diffs)

    @property
    def outcome_changed_tools(self) -> int:
        return sum(diff.outcome_changed for diff in self.tool_diffs)

    @property
    def error_changed_tools(self) -> int:
        return sum(diff.error_changed for diff in self.tool_diffs)

    @property
    def ambiguous_matches(self) -> int:
        return sum(diff.ambiguous for diff in self.tool_diffs)

    @property
    def identity_matches(self) -> int:
        return sum(
            diff.state not in {"added", "removed"}
            and diff.alignment in {"call-id", "span-id", "parent-tool"}
            for diff in self.tool_diffs
        )

    @property
    def position_fallbacks(self) -> int:
        return sum(diff.alignment == "position-fallback" for diff in self.tool_diffs)

    @property
    def total_duration_delta_ms(self) -> float:
        return round(self.run_b["duration_ms"] - self.run_a["duration_ms"], 3)

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_a": self.run_a,
            "run_b": self.run_b,
            "changed_tools": self.changed_tools,
            "added_tools": self.added_tools,
            "removed_tools": self.removed_tools,
            "outcome_changed_tools": self.outcome_changed_tools,
            "error_changed_tools": self.error_changed_tools,
            "total_duration_delta_ms": self.total_duration_delta_ms,
            "ambiguous_matches": self.ambiguous_matches,
            "identity_matches": self.identity_matches,
            "position_fallbacks": self.position_fallbacks,
            "alignment": {
                "strategy": "call-id, span-id, parent-tool, then position",
                "identity_matches": self.identity_matches,
                "position_fallbacks": self.position_fallbacks,
                "ambiguous_matches": self.ambiguous_matches,
            },
            "tool_diffs": [diff.as_dict() for diff in self.tool_diffs],
        }


@dataclass(frozen=True)
class _ToolRef:
    """Internal comparison metadata for one tool span."""

    span: TraceSpan
    order: int
    call_id: str | None
    parent_key: str
    tool_name: str


@dataclass(frozen=True)
class _AlignedPair:
    """One pair of references and the evidence used to align them."""

    index_a: int | None
    index_b: int | None
    alignment: str
    ambiguous: bool = False
    ambiguity_reason: str | None = None
    candidates_a: tuple[str, ...] = ()
    candidates_b: tuple[str, ...] = ()


def compare_runs(run_a: TraceDocument, run_b: TraceDocument) -> CompareReport:
    """Compare tool calls by stable IDs, parent context, and explicit fallbacks."""

    with traced_operation("compare.runs", {"run.a": run_a.run_id, "run.b": run_b.run_id}):
        refs_a = _tool_refs(run_a)
        refs_b = _tool_refs(run_b)
        pairs = _align(refs_a, refs_b)
        diffs: list[ToolDiff] = []
        for index, pair in enumerate(pairs, start=1):
            span_a = refs_a[pair.index_a].span if pair.index_a is not None else None
            span_b = refs_b[pair.index_b].span if pair.index_b is not None else None
            diffs.append(
                _diff(
                    index,
                    span_a,
                    span_b,
                    alignment=pair.alignment,
                    ambiguous=pair.ambiguous,
                    ambiguity_reason=pair.ambiguity_reason,
                    candidates_a=list(pair.candidates_a),
                    candidates_b=list(pair.candidates_b),
                    parent_changed=(
                        pair.index_a is not None
                        and pair.index_b is not None
                        and refs_a[pair.index_a].parent_key != refs_b[pair.index_b].parent_key
                    ),
                )
            )
        return CompareReport(_summary(run_a), _summary(run_b), diffs)


def _diff(
    index: int,
    span_a: TraceSpan | None,
    span_b: TraceSpan | None,
    *,
    alignment: str,
    ambiguous: bool,
    ambiguity_reason: str | None,
    candidates_a: list[str],
    candidates_b: list[str],
    parent_changed: bool = False,
) -> ToolDiff:
    if span_a is None:
        tool_b = span_b.tool_call.name if span_b and span_b.tool_call else span_b.name
        return ToolDiff(
            index,
            None,
            tool_b,
            "added",
            False,
            True,
            True,
            None,
            None,
            _error(span_b),
            error_changed=_error(span_b) is not None,
            span_id_b=span_b.span_id if span_b else None,
            call_id_b=_call_id(span_b),
            parent_span_id_b=span_b.parent_span_id if span_b else None,
            alignment=alignment,
            ambiguous=ambiguous,
            ambiguity_reason=ambiguity_reason,
            candidates_a=candidates_a,
            candidates_b=candidates_b,
        )
    if span_b is None:
        tool_a = span_a.tool_call.name if span_a.tool_call else span_a.name
        return ToolDiff(
            index,
            tool_a,
            None,
            "removed",
            True,
            True,
            True,
            None,
            _error(span_a),
            None,
            error_changed=_error(span_a) is not None,
            span_id_a=span_a.span_id if span_a else None,
            call_id_a=_call_id(span_a),
            parent_span_id_a=span_a.parent_span_id if span_a else None,
            alignment=alignment,
            ambiguous=ambiguous,
            ambiguity_reason=ambiguity_reason,
            candidates_a=candidates_a,
            candidates_b=candidates_b,
        )

    call_a = span_a.tool_call
    call_b = span_b.tool_call
    tool_a = call_a.name if call_a else span_a.name
    tool_b = call_b.name if call_b else span_b.name
    arguments_changed = (call_a.arguments if call_a else {}) != (call_b.arguments if call_b else {})
    outcome_a = call_a.outcome if call_a else span_a.status
    outcome_b = call_b.outcome if call_b else span_b.status
    outcome_changed = outcome_a != outcome_b
    result_changed = canonical_hash(call_a.result if call_a else None) != canonical_hash(
        call_b.result if call_b else None
    )
    names_changed = tool_a != tool_b
    state = "same"
    if names_changed or arguments_changed or outcome_changed or result_changed or parent_changed:
        state = "changed"
    error_a = _error(span_a)
    error_b = _error(span_b)
    return ToolDiff(
        index,
        tool_a,
        tool_b,
        state,
        arguments_changed or names_changed,
        outcome_changed,
        result_changed,
        round(span_b.duration_ms - span_a.duration_ms, 3),
        error_a,
        error_b,
        argument_keys_changed=_changed_keys(
            call_a.arguments if call_a else None, call_b.arguments if call_b else None
        ),
        result_keys_changed=_changed_keys(
            call_a.result if call_a else None, call_b.result if call_b else None
        ),
        error_changed=error_a != error_b,
        span_id_a=span_a.span_id,
        span_id_b=span_b.span_id,
        call_id_a=_call_id(span_a),
        call_id_b=_call_id(span_b),
        parent_span_id_a=span_a.parent_span_id,
        parent_span_id_b=span_b.parent_span_id,
        parent_changed=parent_changed,
        alignment=alignment,
        ambiguous=ambiguous,
        ambiguity_reason=ambiguity_reason,
        candidates_a=candidates_a,
        candidates_b=candidates_b,
    )


def _align(refs_a: list[_ToolRef], refs_b: list[_ToolRef]) -> list[_AlignedPair]:
    """Return deterministic pairs using identity evidence before position."""

    pairs: list[_AlignedPair] = []
    used_a: set[int] = set()
    used_b: set[int] = set()

    for key in _shared_keys(refs_a, refs_b, lambda ref: ref.call_id):
        _add_group_pairs(
            pairs,
            used_a,
            used_b,
            _group_indices(refs_a, key, lambda ref: ref.call_id),
            _group_indices(refs_b, key, lambda ref: ref.call_id),
            "call-id",
            refs_a=refs_a,
            refs_b=refs_b,
            ambiguous_reason="Repeated call ID; order disambiguates this comparison.",
        )

    for key in _shared_keys(refs_a, refs_b, lambda ref: ref.span.span_id):
        indexes_a = [
            index
            for index in _group_indices(refs_a, key, lambda ref: ref.span.span_id)
            if index not in used_a and refs_a[index].call_id is None
        ]
        indexes_b = [
            index
            for index in _group_indices(refs_b, key, lambda ref: ref.span.span_id)
            if index not in used_b and refs_b[index].call_id is None
        ]
        _add_group_pairs(
            pairs,
            used_a,
            used_b,
            indexes_a,
            indexes_b,
            "span-id",
            refs_a=refs_a,
            refs_b=refs_b,
        )

    for key in _shared_keys(refs_a, refs_b, _semantic_key):
        indexes_a = [
            index
            for index in _group_indices(refs_a, key, _semantic_key)
            if index not in used_a
        ]
        indexes_b = [
            index
            for index in _group_indices(refs_b, key, _semantic_key)
            if index not in used_b
        ]
        _add_semantic_pairs(pairs, used_a, used_b, indexes_a, indexes_b, refs_a, refs_b)

    remaining_a = [index for index in range(len(refs_a)) if index not in used_a]
    remaining_b = [index for index in range(len(refs_b)) if index not in used_b]
    shared_remaining_semantics = {
        (refs_a[index].parent_key, refs_a[index].tool_name)
        for index in remaining_a
    } & {
        (refs_b[index].parent_key, refs_b[index].tool_name)
        for index in remaining_b
    }
    if remaining_a and remaining_b and not shared_remaining_semantics and not any(
        refs_a[index].call_id or refs_b[index].call_id
        for index in remaining_a
        for index in remaining_b
    ):
        for index_a, index_b in zip(remaining_a, remaining_b, strict=False):
            pairs.append(
                _AlignedPair(
                    index_a,
                    index_b,
                    "position-fallback",
                    ambiguous=True,
                    ambiguity_reason="No stable call identity matched; recorded position was used.",
                )
            )
            used_a.add(index_a)
            used_b.add(index_b)

    pairs.extend(
        _AlignedPair(index_a, None, "unmatched")
        for index_a in range(len(refs_a))
        if index_a not in used_a
    )
    pairs.extend(
        _AlignedPair(None, index_b, "unmatched")
        for index_b in range(len(refs_b))
        if index_b not in used_b
    )
    return sorted(pairs, key=lambda pair: _pair_sort_key(pair, refs_a, refs_b))


def _add_semantic_pairs(
    pairs: list[_AlignedPair],
    used_a: set[int],
    used_b: set[int],
    indexes_a: list[int],
    indexes_b: list[int],
    refs_a: list[_ToolRef],
    refs_b: list[_ToolRef],
) -> None:
    """Align matching parent/tool groups without hiding missing identity data."""

    no_id_a = [index for index in indexes_a if refs_a[index].call_id is None]
    no_id_b = [index for index in indexes_b if refs_b[index].call_id is None]
    paired: list[tuple[int, int, bool, str | None]] = []
    for index_a, index_b in zip(no_id_a, no_id_b, strict=False):
        paired.append((index_a, index_b, len(no_id_a) != 1 or len(no_id_b) != 1, None))

    if not paired and (no_id_a or no_id_b):
        missing_side = "baseline" if no_id_a else "candidate"
        for index_a, index_b in zip(
            no_id_a or indexes_a,
            no_id_b or indexes_b,
            strict=False,
        ):
            paired.append(
                (
                    index_a,
                    index_b,
                    True,
                    f"{missing_side} call has no explicit call ID.",
                )
            )

    if not paired and indexes_a and indexes_b:
        return

    if len(no_id_a) != len(no_id_b) and paired:
        paired = [
            (index_a, index_b, True, "Repeated tool name lacks a unique call ID.")
            for index_a, index_b, _ambiguous, _reason in paired
        ]

    for index_a, index_b, ambiguous, reason in paired:
        candidates_a = tuple(refs_a[index].span.span_id for index in indexes_a)
        candidates_b = tuple(refs_b[index].span.span_id for index in indexes_b)
        pairs.append(
            _AlignedPair(
                index_a,
                index_b,
                "parent-tool",
                ambiguous=ambiguous,
                ambiguity_reason=reason,
                candidates_a=candidates_a if ambiguous else (),
                candidates_b=candidates_b if ambiguous else (),
            )
        )
        used_a.add(index_a)
        used_b.add(index_b)


def _add_group_pairs(
    pairs: list[_AlignedPair],
    used_a: set[int],
    used_b: set[int],
    indexes_a: list[int],
    indexes_b: list[int],
    alignment: str,
    *,
    refs_a: list[_ToolRef],
    refs_b: list[_ToolRef],
    ambiguous_reason: str | None = None,
) -> None:
    """Pair two identity groups and retain all candidates when identity repeats."""

    ambiguous = len(indexes_a) != 1 or len(indexes_b) != 1
    for index_a, index_b in zip(indexes_a, indexes_b, strict=False):
        pairs.append(
            _AlignedPair(
                index_a,
                index_b,
                alignment,
                ambiguous=ambiguous,
                ambiguity_reason=ambiguous_reason if ambiguous else None,
                candidates_a=(
                    tuple(refs_a[index].span.span_id for index in indexes_a)
                    if ambiguous
                    else ()
                ),
                candidates_b=(
                    tuple(refs_b[index].span.span_id for index in indexes_b)
                    if ambiguous
                    else ()
                ),
            )
        )
        used_a.add(index_a)
        used_b.add(index_b)


def _shared_keys(refs_a: list[_ToolRef], refs_b: list[_ToolRef], key_fn) -> list[Any]:
    keys_a = {key_fn(ref) for ref in refs_a if key_fn(ref) is not None}
    keys_b = {key_fn(ref) for ref in refs_b if key_fn(ref) is not None}
    return sorted(keys_a & keys_b, key=str)


def _group_indices(refs: list[_ToolRef], key: Any, key_fn) -> list[int]:
    return [index for index, ref in enumerate(refs) if key_fn(ref) == key]


def _pair_sort_key(
    pair: _AlignedPair,
    refs_a: list[_ToolRef],
    refs_b: list[_ToolRef],
) -> tuple[int, int]:
    order_a = refs_a[pair.index_a].order if pair.index_a is not None else None
    order_b = refs_b[pair.index_b].order if pair.index_b is not None else None
    if order_a is None:
        order_a = order_b
    if order_b is None:
        order_b = order_a
    assert order_a is not None and order_b is not None
    return min(order_a, order_b), max(order_a, order_b)


def _tool_refs(trace: TraceDocument) -> list[_ToolRef]:
    spans = trace.tool_spans()
    by_id = {span.span_id: span for span in trace.spans}
    cache: dict[str, str] = {}
    refs: list[_ToolRef] = []
    for order, span in enumerate(spans):
        call = span.tool_call
        refs.append(
            _ToolRef(
                span=span,
                order=order,
                call_id=_call_id(span),
                parent_key=_parent_key(span, by_id, cache),
                tool_name=call.name if call else span.name,
            )
        )
    return refs


def _semantic_key(ref: _ToolRef) -> tuple[str, str]:
    return ref.parent_key, ref.tool_name


def _parent_key(
    span: TraceSpan,
    by_id: dict[str, TraceSpan],
    cache: dict[str, str],
    visiting: set[str] | None = None,
) -> str:
    if span.span_id in cache:
        return cache[span.span_id]
    visiting = visiting or set()
    if span.span_id in visiting:
        return f"cycle:{span.span_id}"
    call = span.tool_call
    explicit_parent = _first_value(
        call,
        ("parent_call_id", "parent_id"),
    ) or _first_value(
        span,
        ("parent_call_id", "parent_id", "tool.parent_call_id"),
    ) or _first_value(
        span.attributes,
        ("parent_call_id", "parent.call_id", "tool.parent_call_id"),
    )
    if explicit_parent:
        result = f"call:{explicit_parent}"
    elif span.parent_span_id is None:
        result = "root"
    else:
        parent = by_id.get(span.parent_span_id)
        if parent is None:
            result = f"orphan:{span.parent_span_id}"
        else:
            parent_id = _call_id(parent)
            if parent_id:
                result = f"call:{parent_id}"
            else:
                result = f"path:{_span_path(parent, by_id, visiting | {span.span_id})}"
    cache[span.span_id] = result
    return result


def _span_path(span: TraceSpan, by_id: dict[str, TraceSpan], visiting: set[str]) -> str:
    if span.span_id in visiting:
        return span.name
    if span.parent_span_id is None or span.parent_span_id not in by_id:
        return span.name
    parent = by_id[span.parent_span_id]
    return f"{_span_path(parent, by_id, visiting | {span.span_id})}/{span.name}"


def _call_id(span: TraceSpan | None) -> str | None:
    if span is None:
        return None
    call = span.tool_call
    return (
        _first_value(call, ("call_id", "id", "tool_call_id", "tool.id"))
        if call is not None
        else None
    ) or _first_value(
        span,
        ("call_id", "tool_call_id", "tool.call_id"),
    ) or _first_value(
        span.attributes,
        ("call_id", "call.id", "tool_call_id", "tool.call_id", "workbench.call_id"),
    )


def _first_value(source: Any, keys: tuple[str, ...]) -> str | None:
    if source is None:
        return None
    if isinstance(source, dict):
        values = source
    else:
        values = getattr(source, "model_extra", None) or {}
        if not values:
            values = getattr(source, "__dict__", {})
    for key in keys:
        value = values.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _changed_keys(value_a: Any, value_b: Any) -> list[str]:
    """Return sorted top-level keys whose values differ between two mappings."""
    if not isinstance(value_a, dict) or not isinstance(value_b, dict):
        return []
    keys = set(value_a) | set(value_b)
    return sorted(key for key in keys if value_a.get(key) != value_b.get(key))


def _error(span: TraceSpan | None) -> str | None:
    return span.tool_call.error if span and span.tool_call else None


def _summary(trace: TraceDocument) -> dict[str, Any]:
    return {
        "run_id": trace.run_id,
        "trace_id": trace.trace_id,
        "agent_name": trace.agent_name,
        "agent_version": trace.agent_version,
        "status": trace.status,
        "duration_ms": trace.duration_ms,
        "tool_count": len(trace.tool_spans()),
    }


