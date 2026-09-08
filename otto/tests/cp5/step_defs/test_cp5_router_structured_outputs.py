"""Step definitions for ``features/cp5_router_structured_outputs.feature``.

The BDD contract file is the crew spec's copy, verbatim. Every scenario
exercises the real router package — no scenario asserts against a mock of
the thing under test.
"""

from __future__ import annotations

from pytest_bdd import given, parsers, scenarios, then, when

from otto.router import (
    Claim,
    EvalGate,
    GroundingCheck,
    OutcomeState,
    RouterResponse,
    RouterTask,
)
from otto.router.evals import run_eval_cli
from otto.router.render import UNVERIFIED_PREFIX, render_claims
from otto.tests.cp5.conftest import ScriptedClient, always_5xx, contract_json

scenarios("../features/cp5_router_structured_outputs.feature")


# -- Background ---------------------------------------------------------------


@given(
    parsers.parse(
        "lane budgets judgment {judgment:d}, bulk {bulk:d}, verify {verify:d} "
        "USD per day"
    )
)
def lane_budgets(router_config, judgment: int, bulk: int, verify: int) -> None:
    assert router_config.lanes["judgment"].daily_budget_usd == judgment
    assert router_config.lanes["bulk"].daily_budget_usd == bulk
    assert router_config.lanes["verify"].daily_budget_usd == verify


@given(parsers.parse("on_budget_exhausted is {policy}"))
def budget_policy(router_config, policy: str) -> None:
    assert router_config.on_budget_exhausted == policy


# -- Scenario: eval delta on a policy change ----------------------------------


@given("a change to router policy")
def policy_change(ctx: dict, router_config) -> None:
    ctx["gate"] = EvalGate(config=router_config)
    assert ctx["gate"].merge_word_allowed() is False  # P6: no delta, no merge


@when(parsers.parse('an engineer runs "otto eval diff --baseline <ref>"'))
def run_eval_diff(ctx: dict) -> None:
    code, shown = run_eval_cli(["diff", "--baseline", "main"], ctx["gate"])
    ctx["eval_exit"] = code
    ctx["eval_shown"] = shown


@then("a per-lane eval delta is recorded and shown before any merge word is given")
def eval_delta_recorded(ctx: dict) -> None:
    assert ctx["eval_exit"] == 0
    gate: EvalGate = ctx["gate"]
    assert len(gate.records) == 1  # recorded
    record = gate.records[0]
    assert set(record["per_lane_delta"]) == {"judgment", "bulk", "verify"}
    for lane in ("judgment", "bulk", "verify"):  # shown, per lane
        assert f"lane {lane}:" in ctx["eval_shown"]
    assert gate.merge_word_allowed() is True  # only now may the word be given


# -- Scenario: claim with no evidence is flagged ------------------------------


@given("a model response whose claims array includes an entry with empty evidence_refs")
def response_with_bare_claim(ctx: dict) -> None:
    # The model wrote 'this is verified' and marked itself high-confidence:
    # exactly the self-certification the renderer must ignore.
    ctx["response"] = RouterResponse(
        answer="done",
        claims=(
            Claim(
                text="this is verified: the deploy is green",
                evidence_refs=(),
                confidence="high",
            ),
            Claim(
                text="disk usage is 40 percent",
                evidence_refs=("tool_call_df",),
                confidence="med",
            ),
        ),
        proposed_actions=(),
        unknowns=(),
        lane="bulk",
        model="minimax",
        task_id="01J6XW6M6R2K8Q4S7VZC9T3PBA",
        cost_usd=0.001,
        tokens=120,
    )


@when("it is rendered to Telegram")
def render_to_telegram(ctx: dict) -> None:
    ctx["rendered"] = render_claims(ctx["response"])


@then(
    parsers.parse(
        'that claim is prefixed "unverified:" by the gateway\'s rendering rule'
    )
)
def claim_is_prefixed(ctx: dict) -> None:
    first = ctx["rendered"][0]
    assert first.startswith(UNVERIFIED_PREFIX)
    assert "unverified:" in first.split("the deploy")[0]


@then(
    "this is not a model instruction, it is enforced independent of what the "
    "model wrote"
)
def enforced_independent_of_model(ctx: dict) -> None:
    # The model self-declared 'verified' and confidence 'high'; the marker is
    # applied anyway, because the rule reads only the structural facts.
    first = ctx["rendered"][0]
    assert "this is verified" in first  # the model's words survive as payload
    assert first.startswith(UNVERIFIED_PREFIX)  # ... under the gateway's marker
    # And with no verdict on the response, even the evidence-bearing claim is
    # marked: nothing the model wrote can lift the marker (P1).
    assert ctx["rendered"][1].startswith(UNVERIFIED_PREFIX)


# -- Scenario: ungrounded-claim rate meets the bar ----------------------------


@when(parsers.parse('an engineer runs "otto eval run --suite core"'))
def run_core_suite(ctx: dict, router_config) -> None:
    gate = EvalGate(config=router_config)
    code, shown = run_eval_cli(["run", "--suite", "core"], gate)
    ctx["suite_exit"] = code
    ctx["suite_shown"] = shown
    ctx["suite_result"] = gate.run_core_suite()


@then("the ungrounded-claim rate is below 5 percent")
def rate_below_bar(ctx: dict) -> None:
    assert ctx["suite_exit"] == 0
    assert ctx["suite_result"]["ungrounded_rate"] < 0.05
    assert "PASS" in ctx["suite_shown"]


# -- Scenario: a resolving ref that does not support the claim ----------------


@given("a claim whose evidence_refs point to a real tool_call_id")
def claim_with_real_ref(ctx: dict) -> None:
    ctx["claim"] = Claim(
        text="the database migration completed successfully",
        evidence_refs=("tool_call_042",),
        confidence="high",
    )
    ctx["evidence_store"] = {}


@given("the referenced tool result does not support the claim's text")
def evidence_does_not_support(ctx: dict) -> None:
    # The ref RESOLVES — to a result about something else entirely.
    ctx["evidence_store"]["tool_call_042"] = (
        "weather probe: London 18C, light rain, wind 12 km/h"
    )


@when("the groundedness check runs mechanically")
def run_groundedness(ctx: dict, router_config) -> None:
    checker = GroundingCheck(config=router_config)
    ctx["grounded"] = checker.is_grounded(ctx["claim"], ctx["evidence_store"])


@then("the claim is counted as ungrounded, not as verified merely because a ref exists")
def counted_ungrounded(ctx: dict) -> None:
    assert ctx["grounded"] is False


# -- Scenario: daily budget exhausted -> queue, notify, never degrade ---------


@given(parsers.parse("the bulk lane has spent its full daily budget of {spent:d} USD"))
def bulk_budget_spent(ctx: dict, ledger, spent: int) -> None:
    ledger.charge("bulk", float(spent))
    assert ledger.exhausted("bulk") is True


@when("a new task routes to the bulk lane")
def route_new_bulk_task(ctx: dict, router, notifier) -> None:
    client = ScriptedClient(body=contract_json())
    task = RouterTask(input="summarise the overnight cron output", source="cron")
    ctx["client"] = client
    ctx["outcome"] = router.execute(task, client)
    ctx["notifier"] = notifier


@then("it is queued, not executed")
def queued_not_executed(ctx: dict) -> None:
    assert ctx["outcome"].state is OutcomeState.QUEUED_BUDGET
    assert ctx["outcome"].executed is False


@then("Chidi is notified")
def chidi_notified(ctx: dict) -> None:
    assert len(ctx["notifier"].messages) >= 1
    assert any(ctx["outcome"].task_id in m for m in ctx["notifier"].messages)


@then("it is never silently served by a cheaper or different model to avoid the queue")
def never_silently_served(ctx: dict) -> None:
    assert ctx["client"].calls == []  # no provider was consulted at all
    assert ctx["outcome"].models_called == ()
    assert ctx["outcome"].lane == "bulk"  # and it was never re-routed


# -- Scenario: judgment lane 5xx / timeout ------------------------------------


@given("a task routed to the judgment lane")
def judgment_task(ctx: dict) -> None:
    ctx["task"] = RouterTask(input="review this plan", task_class="code")


@when("the provider returns repeated 5xx responses or times out")
def provider_flaps(ctx: dict, router, notifier) -> None:
    client = always_5xx()
    ctx["client"] = client
    ctx["outcome"] = router.execute(ctx["task"], client)
    ctx["notifier"] = notifier
    ctx["max_retries"] = router.config.retry.max_retries_5xx


@then("the router retries once per lane policy")
def retried_once(ctx: dict) -> None:
    assert ctx["max_retries"] == 1
    assert ctx["outcome"].attempts == 1 + ctx["max_retries"]


@then("after exhausting retries the task is routed to needs_human")
def routed_to_needs_human(ctx: dict) -> None:
    assert ctx["outcome"].state is OutcomeState.NEEDS_HUMAN
    assert "needs_human" in " ".join(ctx["notifier"].messages)


@then("it never silently falls back to a different, unconfigured provider")
def no_silent_fallback(ctx: dict, router) -> None:
    configured = router.config.lanes["judgment"].model
    assert ctx["outcome"].lane == "judgment"
    assert all(m == configured for m in ctx["client"].calls)
    assert set(ctx["outcome"].models_called) == {configured}
