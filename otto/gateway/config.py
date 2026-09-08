"""Gateway configuration — every limit is configurable, none is a bare literal.

LAW 46: no file names where a machine, host or limit lives as a typed
constant. Defaults are named here once; every one of them is overridable at
construction time or by environment variable, so a deployment (or a test)
never has to edit this file to change a limit.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

#: Constitution cap (spec section 6): "Hard cap: 12 core tools in v1."
#: Tool sprawl degrades selection accuracy; raising it is a spec change,
#: not a runtime one — hence a named default, not a hidden literal.
_DEFAULT_MAX_TOOLS = 12

#: Spec section 10 (P5): untrusted context caps effective authority here.
_DEFAULT_TAINT_CEILING = "T1"

#: Spec section 9: tiers requiring the human-gate hook even when the tool
#: itself is not tagged irreversible. T3 always does; a T2 tool may opt in
#: via ToolSpec.irreversible.
_DEFAULT_HUMAN_GATE_TIERS: tuple[str, ...] = ("T3",)


def _int_from_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _str_from_env(name: str, default: str) -> str:
    return os.environ.get(name) or default


@dataclass(frozen=True)
class GatewayConfig:
    """Configuration for one ``ToolGateway`` instance.

    Every field has a default drawn from an environment variable (so a
    deployment configures it without a code change) and a hardcoded
    fallback drawn from the constitution (so a bare checkout is still
    correct). No hostname, path or account lives in this file.
    """

    max_tools: int = field(
        default_factory=lambda: _int_from_env(
            "OTTO_GATEWAY_MAX_TOOLS", _DEFAULT_MAX_TOOLS
        )
    )
    taint_ceiling: str = field(
        default_factory=lambda: _str_from_env(
            "OTTO_GATEWAY_TAINT_CEILING", _DEFAULT_TAINT_CEILING
        )
    )
    human_gate_tiers: tuple[str, ...] = field(
        default_factory=lambda: _DEFAULT_HUMAN_GATE_TIERS
    )

    def __post_init__(self) -> None:
        if self.max_tools < 1:
            raise ValueError("max_tools must be >= 1")
