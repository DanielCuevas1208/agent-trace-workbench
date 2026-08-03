"""Deterministic local replay for recorded tool calls."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .handlers import (
    HandlerConfig,
    ReplayHandler,
    ReplayPolicy,
    SideEffectLevel,
    ToolHandler,
    build_registry,
    side_effect_allowed,
)
from .models import TraceDocument
from .telemetry import traced_operation


@dataclass(frozen=True)
class ReplayStep:
    """The result of replaying one tool call."""

    index: int
    tool_name: str
    mode: str
    recorded_outcome: str
    replayed_outcome: str
    result_match: bool
    arguments: dict[str, Any]
    recorded_result: Any
    replayed_result: Any
    error: str | None = None
    guarded: bool = False
    side_effect_level: str | None = None
    policy: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "tool_name": self.tool_name,
            "mode": self.mode,
            "recorded_outcome": self.recorded_outcome,
            "replayed_outcome": self.replayed_outcome,
            "result_match": self.result_match,
            "arguments": self.arguments,
            "recorded_result": self.recorded_result,
            "replayed_result": self.replayed_result,
            "error": self.error,
            "guarded": self.guarded,
            "side_effect_level": self.side_effect_level,
            "policy": self.policy,
        }


@dataclass(frozen=True)
class ReplayReport:
    """A complete deterministic replay report."""

    run_id: str
    deterministic: bool
    steps: list[ReplayStep]
    policy: str | None = None

    @property
    def total_steps(self) -> int:
        return len(self.steps)

    @property
    def matched_steps(self) -> int:
        return sum(step.result_match for step in self.steps)

    @property
    def failed_steps(self) -> int:
        return sum(step.replayed_outcome == "failure" for step in self.steps)

    @property
    def guarded_steps(self) -> int:
        return sum(step.guarded for step in self.steps)

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "deterministic": self.deterministic,
            "policy": self.policy,
            "total_steps": len(self.steps),
            "matched_steps": self.matched_steps,
            "failed_steps": self.failed_steps,
            "guarded_steps": self.guarded_steps,
            "steps": [step.as_dict() for step in self.steps],
        }


class ReplayEngine:
    """Replay tools with guarded local handlers and recorded-result fallback."""

    def __init__(
        self,
        handlers: dict[str, ReplayHandler] | None = None,
        policy: ReplayPolicy = ReplayPolicy.STRICT,
    ) -> None:
        self.handlers = handlers or {}
        self.policy = policy

    def register(
        self,
        tool_name: str,
        handler: ToolHandler,
        *,
        side_effect: SideEffectLevel = SideEffectLevel.UNKNOWN,
    ) -> None:
        """Register a local handler with a declared side-effect level."""

        self.handlers[tool_name] = ReplayHandler(
            tool_name, side_effect, "inline", func=handler
        )

    def load_config(
        self,
        config: HandlerConfig,
        base_dir: str | Path | None = None,
    ) -> None:
        """Adopt a config policy and register its local handlers."""

        self.policy = config.policy
        self.handlers.update(build_registry(config, base_dir))

    def replay(self, trace: TraceDocument) -> ReplayReport:
        """Replay all tool calls in their recorded order."""

        with traced_operation(
            "replay.run",
            {"run.id": trace.run_id, "replay.policy": self.policy.value},
        ):
            steps: list[ReplayStep] = []
            for index, span in enumerate(trace.tool_spans(), start=1):
                assert span.tool_call is not None
                call = span.tool_call
                handler = self.handlers.get(call.name)
                if handler is None:
                    mode = "recorded-fallback"
                    guarded = False
                    level = None
                    replayed_outcome = call.outcome
                    error = None
                    replayed_result = call.result
                elif not side_effect_allowed(handler.side_effect, self.policy):
                    mode = "guarded"
                    guarded = True
                    level = handler.side_effect.value
                    replayed_outcome = call.outcome
                    error = None
                    replayed_result = call.result
                else:
                    mode = "handler"
                    guarded = False
                    level = handler.side_effect.value
                    replayed_outcome = "success"
                    error = None
                    replayed_result = call.result
                    try:
                        if handler.func is not None:
                            replayed_result = handler.func(call.arguments)
                        else:
                            replayed_result = handler.fixed_result
                    except Exception as exc:  # noqa: BLE001 - report tool failures as data
                        replayed_outcome = "failure"
                        error = str(exc)
                result_match = canonical_hash(call.result) == canonical_hash(replayed_result)
                steps.append(
                    ReplayStep(
                        index=index,
                        tool_name=call.name,
                        mode=mode,
                        recorded_outcome=call.outcome,
                        replayed_outcome=replayed_outcome,
                        result_match=result_match,
                        arguments=call.arguments,
                        recorded_result=call.result,
                        replayed_result=replayed_result,
                        error=error,
                        guarded=guarded,
                        side_effect_level=level,
                        policy=self.policy.value,
                    )
                )
            return ReplayReport(
                run_id=trace.run_id,
                deterministic=True,
                steps=steps,
                policy=self.policy.value,
            )


def canonical_hash(value: Any) -> str:
    """Hash JSON values with stable key order and compact separators."""

    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def default_replay_engine() -> ReplayEngine:
    """Build handlers used by the bundled sample traces."""

    engine = ReplayEngine()
    engine.register(
        "search_catalog",
        lambda args: {
            "query": args.get("query", ""),
            "items": [
                {"sku": "lamp-01", "name": "Desk Lamp", "price": 39.0},
                {"sku": "lamp-02", "name": "Task Light", "price": 52.0},
            ],
        },
        side_effect=SideEffectLevel.READ_ONLY,
    )
    engine.register(
        "get_inventory",
        lambda args: {"sku": args.get("sku"), "available": 12},
        side_effect=SideEffectLevel.READ_ONLY,
    )
    return engine


