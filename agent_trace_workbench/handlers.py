"""Configurable replay handlers and side-effect guards for local replay."""

from __future__ import annotations

import importlib.util
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .telemetry import traced_operation

ToolHandler = Callable[[dict[str, Any]], Any]


class SideEffectLevel(str, Enum):
    """Declared behaviour of a local handler."""

    READ_ONLY = "read_only"
    LOCAL_WRITE = "local_write"
    NETWORK = "network"
    UNKNOWN = "unknown"


class ReplayPolicy(str, Enum):
    """Side-effect budget applied during a deterministic replay."""

    STRICT = "strict"
    LOCAL = "local"
    ALL = "all"


_LEVEL_RANK = {
    SideEffectLevel.READ_ONLY: 0,
    SideEffectLevel.LOCAL_WRITE: 1,
    SideEffectLevel.NETWORK: 2,
    SideEffectLevel.UNKNOWN: 3,
}

_POLICY_LIMITS = {
    ReplayPolicy.STRICT: _LEVEL_RANK[SideEffectLevel.READ_ONLY],
    ReplayPolicy.LOCAL: _LEVEL_RANK[SideEffectLevel.LOCAL_WRITE],
    ReplayPolicy.ALL: _LEVEL_RANK[SideEffectLevel.UNKNOWN],
}


def side_effect_allowed(level: SideEffectLevel, policy: ReplayPolicy) -> bool:
    """Return whether a handler level is allowed under a replay policy.

    Unknown handlers only run under the all policy because their behaviour
    cannot be verified from the configuration alone.
    """

    return _LEVEL_RANK[level] <= _POLICY_LIMITS[policy]


class HandlerEntry(BaseModel):
    """One tool mapping inside a local handler configuration."""

    model_config = ConfigDict(extra="forbid")

    tool: str = Field(min_length=1)
    script: str | None = None
    result: Any = None
    side_effect: SideEffectLevel = SideEffectLevel.UNKNOWN

    @model_validator(mode="after")
    def validate_behavior(self) -> HandlerEntry:
        has_script = self.script is not None
        has_result = self.result is not None
        if has_script == has_result:
            raise ValueError("A handler must define exactly one of script or result.")
        return self


class HandlerConfig(BaseModel):
    """Validated local handler configuration for one replay engine."""

    model_config = ConfigDict(extra="forbid")

    policy: ReplayPolicy = ReplayPolicy.STRICT
    handlers: list[HandlerEntry] = Field(default_factory=list)


@dataclass(frozen=True)
class ReplayHandler:
    """A loaded handler ready to run under a side-effect policy."""

    tool: str
    side_effect: SideEffectLevel
    origin: str
    func: ToolHandler | None = None
    fixed_result: Any = None


def load_handler_config(path: str | Path) -> HandlerConfig:
    """Load and validate a local handler configuration file."""

    with traced_operation("handlers.load_config", {"config.name": Path(path).name}):
        return HandlerConfig.model_validate_json(Path(path).read_text(encoding="utf-8"))


def build_registry(
    config: HandlerConfig,
    base_dir: str | Path | None = None,
) -> dict[str, ReplayHandler]:
    """Resolve config entries into runnable local handlers.

    Script paths are relative to the config directory when base_dir is not
    provided. Each script must expose a run(arguments) function.
    """

    with traced_operation("handlers.build_registry", {"handler.count": len(config.handlers)}):
        registry: dict[str, ReplayHandler] = {}
        for entry in config.handlers:
            if entry.tool in registry:
                raise ValueError(f"Duplicate handler for tool: {entry.tool}")
            origin = f"config:{entry.tool}"
            if entry.script is not None:
                script_path = _resolve_script(entry.script, base_dir)
                func = _load_script_function(script_path, entry.tool)
                registry[entry.tool] = ReplayHandler(
                    entry.tool, entry.side_effect, origin, func=func
                )
            else:
                registry[entry.tool] = ReplayHandler(
                    entry.tool, entry.side_effect, origin, fixed_result=entry.result
                )
        return registry


def _resolve_script(script: str, base_dir: str | Path | None) -> Path:
    path = Path(script)
    if not path.is_absolute():
        base = Path(base_dir) if base_dir is not None else Path.cwd()
        path = base / path
    if not path.is_file():
        raise FileNotFoundError(f"Handler script not found: {path}")
    return path


def _load_script_function(path: Path, tool: str) -> ToolHandler:
    spec = importlib.util.spec_from_file_location(_module_name(tool), path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Handler script could not be loaded: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    func = getattr(module, "run", None)
    if not callable(func):
        raise ValueError(f"Handler script must define a run(arguments) function: {path}")
    return func


def _module_name(tool: str) -> str:
    stem = re.sub(r"\W", "_", tool).lower()
    if not stem:
        stem = "handler"
    if stem[0].isdigit():
        stem = f"_{stem}"
    return f"_atw_handlers_{stem}"
