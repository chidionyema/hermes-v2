"""Router configuration — lane policy, budgets and retries are config, never constants.

The founder's ruling (2026-08-31, "configurable obvs") and LAW 46 both bind
here: every threshold has a named default drawn from the spec, an
environment-variable override, and a loader for the versioned-YAML policy
file the spec calls for (section 5, hot-reloadable). Nothing in this file
names a host, a path or a credential.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# Spec section 5 defaults. Named once here; every one is overridable per
# deployment via environment or the YAML policy document.
_DEFAULT_DAILY_BUDGET_USD = {"judgment": 15.0, "bulk": 5.0, "verify": 3.0}
_DEFAULT_MAX_COST_PER_TASK_USD = {"judgment": 0.80, "bulk": 0.10, "verify": 0.10}
#: Bulk lane is MiniMax (fast raw execution — founder-verified lane); the
#: judgment lane is deliberately a different model family so bulk-lane
#: error modes do not correlate with the lane that judges them.
_DEFAULT_LANE_MODELS = {
    "judgment": "anthropic/claude",
    "bulk": "minimax",
    "verify": "google/gemini",
}
_DEFAULT_LANE_FAMILIES = {
    "judgment": "anthropic",
    "bulk": "minimax",
    "verify": "google",
}
_DEFAULT_ON_BUDGET_EXHAUSTED = "queue_and_notify"
_DEFAULT_MAX_RETRIES_5XX = 1
_DEFAULT_MAX_RETRIES_TIMEOUT = 1
_DEFAULT_TIMEOUT_SECONDS = 120.0
#: A timed-out attempt still consumed provider work and bandwidth; it is
#: charged to the lane at this estimate so a timeout loop cannot spend for
#: free (the founder's network-failure word: budget-charged retry).
_DEFAULT_TIMEOUT_CHARGE_USD = 0.01
_DEFAULT_COST_PER_1K_TOKENS_USD = 0.001
#: Groundedness: fraction of a claim's significant tokens that must appear
#: in the referenced evidence for the mechanical check to call it supported.
_DEFAULT_GROUNDING_MIN_OVERLAP = 0.5
_DEFAULT_UNGROUNDED_RATE_BAR = 0.05


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class RetryPolicy:
    """Bounded retries per failure class. Egress denial never retries:
    a deliberate network policy cannot be waited out, so the router fails
    closed on the first refusal."""

    max_retries_5xx: int = field(
        default_factory=lambda: _int_env(
            "OTTO_ROUTER_MAX_RETRIES_5XX", _DEFAULT_MAX_RETRIES_5XX
        )
    )
    max_retries_timeout: int = field(
        default_factory=lambda: _int_env(
            "OTTO_ROUTER_MAX_RETRIES_TIMEOUT", _DEFAULT_MAX_RETRIES_TIMEOUT
        )
    )
    timeout_seconds: float = field(
        default_factory=lambda: _float_env(
            "OTTO_ROUTER_TIMEOUT_SECONDS", _DEFAULT_TIMEOUT_SECONDS
        )
    )
    timeout_charge_usd: float = field(
        default_factory=lambda: _float_env(
            "OTTO_ROUTER_TIMEOUT_CHARGE_USD", _DEFAULT_TIMEOUT_CHARGE_USD
        )
    )


@dataclass(frozen=True)
class LaneConfig:
    """One lane: which model serves it and what it may spend."""

    name: str
    model: str
    family: str
    daily_budget_usd: float
    max_cost_per_task_usd: float
    cost_per_1k_tokens_usd: float = _DEFAULT_COST_PER_1K_TOKENS_USD


def _default_lanes() -> dict[str, LaneConfig]:
    lanes: dict[str, LaneConfig] = {}
    for name, budget in _DEFAULT_DAILY_BUDGET_USD.items():
        lanes[name] = LaneConfig(
            name=name,
            model=os.environ.get(
                f"OTTO_ROUTER_LANE_{name.upper()}_MODEL", _DEFAULT_LANE_MODELS[name]
            ),
            family=_DEFAULT_LANE_FAMILIES[name],
            daily_budget_usd=_float_env(
                f"OTTO_ROUTER_BUDGET_{name.upper()}_USD", budget
            ),
            max_cost_per_task_usd=_float_env(
                f"OTTO_ROUTER_TASK_CAP_{name.upper()}_USD",
                _DEFAULT_MAX_COST_PER_TASK_USD[name],
            ),
            cost_per_1k_tokens_usd=_float_env(
                f"OTTO_ROUTER_PRICE_{name.upper()}_PER_1K_USD",
                _DEFAULT_COST_PER_1K_TOKENS_USD,
            ),
        )
    return lanes


def _default_routes() -> tuple[tuple[dict[str, str], str], ...]:
    # Spec section 5 route table, most specific first, default last.
    return (
        ({"source": "cron"}, "bulk"),
        ({"class": "code"}, "judgment"),
        ({"class": "research", "complexity": "low"}, "bulk"),
    )


@dataclass(frozen=True)
class RouterConfig:
    """The whole routing policy: lanes, routes, guards, retries.

    ``from_policy_dict`` accepts the parsed versioned-YAML policy document
    (spec section 5) so a deployment hot-reloads policy without touching
    code; the dataclass defaults keep a bare checkout correct.
    """

    lanes: dict[str, LaneConfig] = field(default_factory=_default_lanes)
    routes: tuple[tuple[dict[str, str], str], ...] = field(
        default_factory=_default_routes
    )
    default_lane: str = "judgment"
    on_budget_exhausted: str = field(
        default_factory=lambda: os.environ.get(
            "OTTO_ROUTER_ON_BUDGET_EXHAUSTED", _DEFAULT_ON_BUDGET_EXHAUSTED
        )
    )
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    grounding_min_overlap: float = field(
        default_factory=lambda: _float_env(
            "OTTO_ROUTER_GROUNDING_MIN_OVERLAP", _DEFAULT_GROUNDING_MIN_OVERLAP
        )
    )
    ungrounded_rate_bar: float = field(
        default_factory=lambda: _float_env(
            "OTTO_ROUTER_UNGROUNDED_RATE_BAR", _DEFAULT_UNGROUNDED_RATE_BAR
        )
    )

    def __post_init__(self) -> None:
        families = {
            self.lanes[n].family for n in ("judgment", "bulk") if n in self.lanes
        }
        if len(families) == 1:
            msg = (
                "policy defect: judgment and bulk lanes share one model family; "
                "the spec requires distinct families so errors do not correlate"
            )
            raise ValueError(msg)

    @classmethod
    def from_policy_dict(cls, policy: dict) -> RouterConfig:
        """Build a config from a parsed policy document (spec section 5 YAML)."""
        base = cls()
        lanes = dict(base.lanes)
        for name, row in (policy.get("lanes") or {}).items():
            fallback = lanes.get(
                name,
                LaneConfig(
                    name=name,
                    model=name,
                    family=name,
                    daily_budget_usd=0.0,
                    max_cost_per_task_usd=0.0,
                ),
            )
            lanes[name] = LaneConfig(
                name=name,
                model=row.get("model", fallback.model),
                family=row.get("family", fallback.family),
                daily_budget_usd=float(
                    (policy.get("guards", {}).get("daily_budget_usd", {})).get(
                        name, fallback.daily_budget_usd
                    )
                ),
                max_cost_per_task_usd=float(
                    row.get("max_cost_per_task_usd", fallback.max_cost_per_task_usd)
                ),
                cost_per_1k_tokens_usd=float(
                    row.get("cost_per_1k_tokens_usd", fallback.cost_per_1k_tokens_usd)
                ),
            )
        routes: list[tuple[dict[str, str], str]] = []
        default_lane = base.default_lane
        for row in policy.get("routes") or []:
            if "default" in row:
                default_lane = row["default"]
            else:
                routes.append((dict(row["match"]), row["lane"]))
        guards = policy.get("guards") or {}
        return cls(
            lanes=lanes,
            routes=tuple(routes) if routes else base.routes,
            default_lane=default_lane,
            on_budget_exhausted=guards.get(
                "on_budget_exhausted", base.on_budget_exhausted
            ),
            retry=base.retry,
            grounding_min_overlap=base.grounding_min_overlap,
            ungrounded_rate_bar=base.ungrounded_rate_bar,
        )
