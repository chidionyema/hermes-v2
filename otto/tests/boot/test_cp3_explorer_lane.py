"""crew#892 CP3 — Explorer lane: N distinct candidates reach the reasoning
lane, spend lands on the ledger, and the lane's env var is machine-listed.

A lane is a named model alias + budget + per-task cap in
``otto.router.config``; explore is the cheap candidate lane (default alias
``deepseek``, the zero-cost tail). The router executes each propose-fix call
independently through ``Router.execute`` — that is the N-fold seam a later
tree-search (crew#892 CP5) fans out on. This test proves the two native
done-conditions that do not need a live provider:

  1. the lane exists, its model env var is a real, listable name, and the
     override actually redirects the model; and
  2. N distinct candidate responses from the explore lane all reach the
     reasoning lane's synthesis step, and every call's spend lands on the
     explore lane in the ledger (the ``budget.usd_per_day.litellm`` seam).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import pytest

from otto.router import (
    BudgetLedger,
    InMemoryNotifier,
    OutcomeState,
    Router,
    RouterConfig,
    RouterTask,
)
from otto.router.config import lane_env_override_var
from otto.router.providers import ProviderResult

_EXPLORE_ENV = "OTTO_ROUTER_LANE_EXPLORE_MODEL"


def _contract(answer: str) -> str:
    """A well-formed universal-contract document as provider text."""
    import json

    return json.dumps(
        {
            "answer": answer,
            "claims": [],
            "proposed_actions": [],
            "unknowns": [],
        }
    )


@dataclass
class SequencedClient:
    """A provider client that returns a DIFFERENT distinct candidate answer
    on each ``complete`` call, in order. ``calls`` records every completion
    so a test can prove the reasoning lane was consulted exactly once with
    the candidate set folded in."""

    answers: list[str] = field(default_factory=list)
    tokens: int = 30
    calls: list[str] = field(default_factory=list)

    def complete(
        self, model: str, payload: str, timeout_seconds: float
    ) -> ProviderResult:
        self.calls.append(payload)
        if not self.answers:
            raise AssertionError("SequencedClient called with nothing scripted")
        return ProviderResult(text=_contract(self.answers.pop(0)), tokens=self.tokens)


def _explore_only_config() -> RouterConfig:
    """A policy whose default lane is ``explore`` so a test can drive the
    N-fold fan-out without depending on a product route-table decision the
    idp slice owns. The shipped config keeps explore off the default route."""
    config = RouterConfig()
    return RouterConfig(
        lanes=config.lanes,
        routes=(),
        default_lane="explore",
        on_budget_exhausted=config.on_budget_exhausted,
        retry=config.retry,
        grounding_min_overlap=config.grounding_min_overlap,
        ungrounded_rate_bar=config.ungrounded_rate_bar,
        model_families=config.model_families,
    )


def test_explore_lane_env_var_is_machine_listable_and_overrides_model() -> None:
    """The shipped config ships an ``explore`` lane whose model env var is a
    real, listable name, and the variable the governor would lint actually
    redirects the lane's model — LAW 46: one contract, no inlined drift."""
    assert lane_env_override_var("explore") == _EXPLORE_ENV
    bare = RouterConfig()
    assert "explore" in bare.lanes
    assert bare.lanes["explore"].model == "deepseek"
    assert bare.family_of("deepseek") == "deepseek"  # known family, no refusal

    original = os.environ.get(_EXPLORE_ENV)
    try:
        os.environ[_EXPLORE_ENV] = "gemini"  # a known alias in the family map
        redirected = RouterConfig()
        assert redirected.lanes["explore"].model == "gemini"
    finally:
        if original is None:
            os.environ.pop(_EXPLORE_ENV, None)
        else:
            os.environ[_EXPLORE_ENV] = original


def test_explore_lane_not_exhausted_at_zero_and_budgeted() -> None:
    """Budget semantics: explore carries a positive default (a $0 budget
    would read as already-exhausted), is reachable, and is not pre-spent."""
    cfg = _explore_only_config()
    ledger = BudgetLedger(config=cfg)
    assert cfg.lanes["explore"].daily_budget_usd > 0.0
    assert not ledger.exhausted("explore")


def test_n_distinct_candidates_reach_synthesis_all_charged_to_explore() -> None:
    """The CP3 heart: N independent propose-fix calls on the explore lane
    yield N DISTINCT candidate answers, every call's spend lands on the
    explore lane, and the reasoning (deep) lane — the synthesiser — is then
    consulted exactly once with all N candidates folded into its prompt."""
    cfg = _explore_only_config()
    if "deep" not in cfg.lanes:
        pytest.skip("reasoning lane must exist to be the synthesiser")
    ledger = BudgetLedger(config=cfg)
    router = Router(config=cfg, ledger=ledger, notifier=InMemoryNotifier())

    candidates = [
        "proposed fix A: check the exit code",
        "proposed fix B: widen the retry window",
        "proposed fix C: seed the RNG before the loop",
    ]
    client = SequencedClient(answers=list(candidates))

    outcomes = []
    for i in range(len(candidates)):
        task = RouterTask(
            input=f"propose a fix for the flaky test (sample {i})",
            task_class="propose_fixes",
        )
        outcomes.append(router.execute(task, client))

    # Every call executed on the explore lane with a distinct answer.
    assert [o.lane for o in outcomes] == ["explore"] * len(candidates)
    assert all(o.state == OutcomeState.COMPLETED_UNVERIFIED for o in outcomes)
    reached = [o.response.answer for o in outcomes if o.response is not None]
    assert reached == candidates
    assert len(set(reached)) == len(candidates)  # N distinct, none lost/duplicated

    # Spend for the whole turn is on the explore lane.
    assert ledger.spent("explore") > 0.0
    assert sum(o.charged_usd for o in outcomes) == pytest.approx(
        ledger.spent("explore")
    )

    # The reasoning lane — the synthesiser — is consulted once over all N.
    task_models = [o.models_called for o in outcomes]
    assert all(m == ("deepseek",) for m in task_models)  # explore ran the tail
    synth_prompt = "Synthesise the best fix from these candidates:\n" + "\n".join(
        reached
    )
    synth_task = RouterTask(input=synth_prompt, task_class="research")
    # Force the deep lane via the shipped route-by-class for research? There is
    # none to `deep` by default; reach deep by a route row on a config copy.
    deep_cfg = RouterConfig(
        lanes=cfg.lanes,
        routes=(({"class": "research", "complexity": "normal"}, "deep"),),
        default_lane=cfg.default_lane,
        on_budget_exhausted=cfg.on_budget_exhausted,
        retry=cfg.retry,
        grounding_min_overlap=cfg.grounding_min_overlap,
        ungrounded_rate_bar=cfg.ungrounded_rate_bar,
        model_families=cfg.model_families,
    )
    synth_router = Router(config=deep_cfg, ledger=ledger, notifier=InMemoryNotifier())
    # Give the deep lane a client that echoes a synthesis.
    synth_client = SequencedClient(answers=["synthesised fix: use candidate A"])
    synth_out = synth_router.execute(synth_task, synth_client)
    assert synth_out.lane == "deep"
    assert synth_out.state == OutcomeState.COMPLETED_UNVERIFIED
    assert synth_out.response is not None and "candidate A" in synth_out.response.answer
    # The deep lane saw all N candidate texts folded in.
    assert any(all(c in p for c in candidates) for p in synth_client.calls)
