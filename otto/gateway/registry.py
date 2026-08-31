"""Tool registry: at most ``config.max_tools`` tools, each with a strict
JSON Schema for its input (spec section 6).

Registering a tool past the cap refuses (raises ``ToolCapacityExceeded``)
rather than silently evicting or silently accepting a 13th tool — the cap
is a constitution invariant, not a soft limit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Callable

import jsonschema
from jsonschema.validators import validator_for

from otto.gateway.config import GatewayConfig
from otto.gateway.errors import DuplicateTool, SchemaViolation, ToolCapacityExceeded


class Tier(IntEnum):
    """Authority tiers, spec section 9. Ordered so ``Tier.T2 > Tier.T0``
    holds and a tier comparison is a plain integer comparison."""

    T0 = 0
    T1 = 1
    T2 = 2
    T3 = 3

    @classmethod
    def parse(cls, value: "Tier | str") -> "Tier":
        if isinstance(value, Tier):
            return value
        try:
            return cls[str(value).upper()]
        except KeyError as exc:
            raise ValueError(f"unknown tier {value!r}; expected one of T0..T3") from exc


ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]


def _default_handler(_args: dict[str, Any]) -> dict[str, Any]:
    return {}


@dataclass(frozen=True)
class ToolSpec:
    """One entry in the registry.

    ``irreversible`` marks a tool that must go through the human-gate hook
    even outside T3 (spec section 9 allows a T2 action such as PR creation
    to be treated as effectively irreversible by policy; the task brief
    calls this out explicitly as "T3/irreversible"). T3 tools always
    require the human gate regardless of this flag — see
    ``GatewayConfig.human_gate_tiers``.
    """

    name: str
    tier: Tier
    input_schema: dict[str, Any]
    handler: ToolHandler = _default_handler
    irreversible: bool = False
    idempotent: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "tier", Tier.parse(self.tier))
        try:
            validator_cls = validator_for(self.input_schema)
            validator_cls.check_schema(self.input_schema)
        except jsonschema.exceptions.SchemaError as exc:
            raise SchemaViolation(
                f"tool {self.name!r} has an invalid input_schema: {exc.message}"
            ) from exc


@dataclass
class ToolRegistry:
    config: GatewayConfig = field(default_factory=GatewayConfig)
    _tools: dict[str, ToolSpec] = field(default_factory=dict)

    def register(self, tool: ToolSpec) -> None:
        if tool.name in self._tools:
            raise DuplicateTool(tool.name)
        if len(self._tools) >= self.config.max_tools:
            raise ToolCapacityExceeded(self.config.max_tools)
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def names(self) -> list[str]:
        return list(self._tools)
