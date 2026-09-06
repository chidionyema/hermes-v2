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
_DEFAULT_DAILY_BUDGET_USD = {"judgment": 15.0, "bulk": 5.0, "verify": 3.0, "deep": 10.0}
_DEFAULT_MAX_COST_PER_TASK_USD = {
    "judgment": 0.80,
    "bulk": 0.10,
    "verify": 0.10,
    "deep": 0.50,
}
#: Bulk lane is MiniMax (fast raw execution — founder-verified lane); the
#: judgment lane is deliberately a different model family so bulk-lane
#: error modes do not correlate with the lane that judges them.
#: The deep lane is the reasoning lane, reached by name (`kimi`) and not
#: by vendor path: the estate router resolves it through
#: `router_settings.model_group_alias` (idp llm/config.base.yaml), so the
#: day Moonshot ships k4 the rename happens once, at the router, and no
#: caller changes. The lane exists because the route table cannot tell a
#: hard question from an easy one; the operator says so with a `/think`
#: prefix (otto/boot/pipeline.py).
_DEFAULT_LANE_MODELS = {
    "judgment": "anthropic/claude",
    "bulk": "minimax",
    "verify": "google/gemini",
    "deep": "kimi",
}
#: Family is DERIVED from the model name via this explicit mapping — never
#: declared alongside it, so a label can never disagree with the model
#: actually configured (verifier finding on crew#768: a declared label let
#: judgment run the bulk model while validation read "anthropic"). A model
#: absent from this mapping REFUSES at validation — fail closed, never a
#: defaulted family. Extendable via the policy document's model_families.
_DEFAULT_MODEL_FAMILIES = {
    "anthropic/claude": "anthropic",
    "claude": "anthropic",
    "minimax": "minimax",
    "minimax/minimax-01": "minimax",
    "minimax_m27": "minimax",
    "google/gemini": "google",
    "gemini": "google",
    "deepseek": "deepseek",
    "kimi": "moonshot",
    "moonshot/kimi-k3": "moonshot",
    # The estate router's neutral aliases. A lane is configured with a name on
    # the router, never a vendor path (LAW 34), so every name idp's
    # llm/config.yaml serves has to derive to a family or this class of refusal
    # comes back the next time a lane is pointed at one. On 2026-09-05 the two
    # Otto doors had their bulk lane on "fast"; it was in no mapping, so
    # family_of refused the whole configuration and the gateway answered
    # nothing on every inbound message. The family is the vendor the alias
    # currently resolves to in that file, which is what the distinct-family
    # guard is actually asking about: correlated error modes.
    "fast": "google",  # gemini/gemini-2.5-flash
    "default": "google",  # gemini/gemini-2.5-pro
    "vision": "google",  # gemini/gemini-2.5-flash
    "image": "google",  # gemini/gemini-3.1-flash-image
    "embed": "google",  # gemini/gemini-embedding-001
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


def _merge_model_families(
    base: dict[str, str], overrides: dict[str, str]
) -> dict[str, str]:
    """Merge a policy document's ``model_families`` onto the shipped
    defaults. A policy may ADD a model the defaults do not know about; it
    may never CHANGE a shipped entry's family — that is the exact route the
    family guard exists to close (a policy that remaps
    ``minimax/minimax-01`` to a non-minimax family re-legalizes putting the
    judgment lane on the real MiniMax model). Redefinition REFUSES the whole
    config, fail closed, naming the model and both family values — never a
    silent overwrite."""
    merged = dict(base)
    for model, family in overrides.items():
        if model in base and base[model] != family:
            msg = (
                f"policy defect: model_families redefines the shipped entry "
                f"for '{model}' from '{base[model]}' to '{family}'; a policy "
                "document may add a new model->family entry but may never "
                "change a shipped default (refusing the config, fail closed)"
            )
            raise ValueError(msg)
        merged[model] = family
    return merged


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
    """One lane: which model serves it and what it may spend.

    Deliberately NO ``family`` field: family is derived from ``model`` by
    ``RouterConfig.family_of`` so no declared label can disagree with the
    model actually configured.
    """

    name: str
    model: str
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
        ({"class": "deep"}, "deep"),
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
    model_families: dict[str, str] = field(
        default_factory=lambda: dict(_DEFAULT_MODEL_FAMILIES)
    )

    def family_of(self, model: str) -> str:
        """Derive a model's family from the explicit mapping. An unknown
        model REFUSES — fail closed, never a defaulted family (a default
        would let an unmapped model slip past the distinct-family guard)."""
        try:
            return self.model_families[model]
        except KeyError:
            msg = (
                f"policy defect: model '{model}' is in no family mapping; "
                "refusing the config (add it to model_families, never default)"
            )
            raise ValueError(msg) from None

    def lane_family(self, lane: str) -> str:
        return self.family_of(self.lanes[lane].model)

    def __post_init__(self) -> None:
        # Every configured lane's model must derive to a family (unknown
        # model = refuse), whatever env override or policy document set it.
        derived = {name: self.family_of(cfg.model) for name, cfg in self.lanes.items()}
        for a, b in (("judgment", "bulk"), ("judgment", "verify")):
            if a in derived and b in derived and derived[a] == derived[b]:
                msg = (
                    f"policy defect: {a} and {b} lanes share one model family "
                    f"('{derived[a]}', derived from the configured models); the "
                    "spec requires distinct families so errors do not correlate"
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
                    daily_budget_usd=0.0,
                    max_cost_per_task_usd=0.0,
                ),
            )
            lanes[name] = LaneConfig(
                name=name,
                model=row.get("model", fallback.model),
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
            model_families=_merge_model_families(
                base.model_families, policy.get("model_families") or {}
            ),
        )
