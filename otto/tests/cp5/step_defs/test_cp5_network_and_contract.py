"""Step definitions for ``features/cp5_network_and_contract.feature``.

The founder's explicit network-failure word: slow provider, 5xx flap,
egress denied — each covered, each fail-closed. Plus contract refusal and
the P1 never-self-certified guarantee.
"""

from __future__ import annotations

import json

from pytest_bdd import given, scenarios, then, when

import pytest

from otto.router import OutcomeState, RouterConfig, RouterTask, VerificationStatus
from otto.tests.cp5.conftest import (
    ScriptedClient,
    always_timeout,
    contract_json,
    egress_denied,
)

scenarios("../features/cp5_network_and_contract.feature")


@given("a task routed to the bulk lane")
def bulk_task(ctx: dict) -> None:
    ctx["task"] = RouterTask(input="summarise these logs", source="cron")


@given("a provider that never answers inside the timeout")
def slow_provider(ctx: dict) -> None:
    ctx["client"] = always_timeout()


@given("a provider whose egress is denied by network policy")
def denied_provider(ctx: dict) -> None:
    ctx["client"] = egress_denied()


@given("a provider that answers with output missing the claims array")
def malformed_provider(ctx: dict) -> None:
    body = json.dumps({"answer": "done", "proposed_actions": [], "unknowns": []})
    ctx["client"] = ScriptedClient(body=body)


@given("a provider that answers with a well-formed contract document")
def wellformed_provider(ctx: dict) -> None:
    claims = [
        {
            "text": "cron output summarised",
            "evidence_refs": ["tool_call_001"],
            "confidence": "high",
        }
    ]
    ctx["client"] = ScriptedClient(body=contract_json(claims=claims))


@given("a provider whose answer costs more than the bulk per-task cap")
def expensive_provider(ctx: dict, router) -> None:
    lane = router.config.lanes["bulk"]
    # Enough tokens that tokens/1000 * price > max_cost_per_task_usd.
    tokens = int(lane.max_cost_per_task_usd / lane.cost_per_1k_tokens_usd * 1000) + 1000
    ctx["client"] = ScriptedClient(body=contract_json(), tokens=tokens)


@when("the router executes the task")
def execute_task(ctx: dict, router, notifier, ledger) -> None:
    ctx["outcome"] = router.execute(ctx["task"], ctx["client"])
    ctx["notifier"] = notifier
    ctx["ledger"] = ledger
    ctx["router"] = router


@then("each timed-out attempt is charged to the lane budget")
def timeout_charged(ctx: dict) -> None:
    charge = ctx["router"].config.retry.timeout_charge_usd
    attempts = ctx["outcome"].attempts
    assert attempts >= 2
    assert ctx["ledger"].spent("bulk") == attempts * charge
    assert ctx["outcome"].charged_usd == attempts * charge


@then("the router retried exactly once before pausing")
def retried_once_then_paused(ctx: dict) -> None:
    assert ctx["router"].config.retry.max_retries_timeout == 1
    assert ctx["outcome"].attempts == 2


@then("the task is routed to needs_human and Chidi is notified")
def needs_human_and_notified(ctx: dict) -> None:
    assert ctx["outcome"].state is OutcomeState.NEEDS_HUMAN
    assert any(ctx["outcome"].task_id in m for m in ctx["notifier"].messages)


@then("the task is routed to needs_human after a single attempt")
def needs_human_single_attempt(ctx: dict) -> None:
    assert ctx["outcome"].state is OutcomeState.NEEDS_HUMAN
    assert ctx["outcome"].attempts == 1


@then("no retry and no fallback provider was attempted")
def no_retry_no_fallback(ctx: dict) -> None:
    configured = ctx["router"].config.lanes["bulk"].model
    assert ctx["client"].calls == [configured]  # one call, the configured model


@then("Chidi is notified")
def chidi_notified(ctx: dict) -> None:
    assert any(ctx["outcome"].task_id in m for m in ctx["notifier"].messages)


@then("the outcome is refused_malformed and no response object exists")
def refused_malformed(ctx: dict) -> None:
    assert ctx["outcome"].state is OutcomeState.REFUSED_MALFORMED
    assert ctx["outcome"].response is None


@then("the missing field was not silently defaulted")
def not_defaulted(ctx: dict) -> None:
    assert "claims" in ctx["outcome"].reason  # the refusal names the field
    assert ctx["outcome"].executed is False


@then("the normalised response carries verification status unverified")
def status_unverified(ctx: dict) -> None:
    assert ctx["outcome"].state is OutcomeState.COMPLETED_UNVERIFIED
    response = ctx["outcome"].response
    assert response is not None
    assert response.verification is VerificationStatus.UNVERIFIED


@then("no field the provider can emit produces a verified response")
def provider_cannot_self_certify(ctx: dict, router) -> None:
    # A provider document that claims to be verified, with every extra flag
    # it can invent, still normalises to UNVERIFIED.
    body = json.dumps(
        {
            "answer": "all done",
            "claims": [],
            "proposed_actions": [],
            "unknowns": [],
            "verification": "verified",
            "verified": True,
            "status": "completed",
        }
    )
    task = RouterTask(input="x", source="cron")
    outcome = router.execute(task, ScriptedClient(body=body))
    assert outcome.response is not None
    assert outcome.response.verification is VerificationStatus.UNVERIFIED


@then("the outcome is paused_task_budget and Chidi is notified")
def paused_task_budget(ctx: dict) -> None:
    assert ctx["outcome"].state is OutcomeState.PAUSED_TASK_BUDGET
    assert any("per-task cap" in m for m in ctx["notifier"].messages)


@then("the overrun spend is still recorded on the lane ledger")
def overrun_still_charged(ctx: dict) -> None:
    assert ctx["ledger"].spent("bulk") > 0
    assert ctx["ledger"].spent("bulk") == ctx["outcome"].charged_usd


# -- distinct-family guard: derived from the model, fail closed ---------------
# (verifier finding, crew#768 comment 5485570153: the declared label let
# OTTO_ROUTER_LANE_JUDGMENT_MODEL=minimax through; these steps reproduce
# that exact exploit and its two neighbours against the derived guard)


@given("the environment sets the judgment lane model to minimax")
def env_judgment_is_bulk_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTTO_ROUTER_LANE_JUDGMENT_MODEL", "minimax")


@given("the environment sets the judgment lane model to another minimax-family model")
def env_judgment_is_bulk_family(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTTO_ROUTER_LANE_JUDGMENT_MODEL", "minimax/minimax-01")


@given(
    "the environment sets the judgment lane model to a model absent from the "
    "family mapping"
)
def env_judgment_is_unmapped_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTTO_ROUTER_LANE_JUDGMENT_MODEL", "mystery-model-9000")


@when("the router config is validated")
def validate_router_config(ctx: dict) -> None:
    try:
        ctx["config"] = RouterConfig()
        ctx["config_error"] = None
    except ValueError as exc:
        ctx["config"] = None
        ctx["config_error"] = str(exc)


@then("the config is refused because judgment and bulk derive to one model family")
def refused_for_shared_family(ctx: dict) -> None:
    assert ctx["config"] is None
    assert ctx["config_error"] is not None
    assert "judgment and bulk lanes share one model family" in ctx["config_error"]
    assert "derived from the configured models" in ctx["config_error"]


@then("the config is refused because the model derives to no family")
def refused_for_unknown_model(ctx: dict) -> None:
    assert ctx["config"] is None
    assert ctx["config_error"] is not None
    assert "no family mapping" in ctx["config_error"]
    assert "mystery-model-9000" in ctx["config_error"]


# -- model_families merge guard: a policy may add, never redefine ------------
# (verifier finding, crew#768 comment 5485683430: from_policy_dict merged a
# policy document's model_families OVER the shipped defaults, so a policy
# could remap minimax/minimax-01 to a non-minimax family and re-legalize the
# exact exploit the distinct-family guard exists to refuse)


@given("a policy document that redefines minimax/minimax-01 to family not-minimax")
def policy_redefines_shipped_entry(ctx: dict) -> None:
    ctx["policy"] = {"model_families": {"minimax/minimax-01": "not-minimax"}}


@given("a policy document that adds a brand-new model mapped to a brand-new family")
def policy_adds_new_entry(ctx: dict) -> None:
    ctx["policy"] = {
        "model_families": {"newvendor/newmodel-1": "newvendor"},
        "lanes": {},
    }


@given("the policy routes the judgment lane to that brand-new model")
def policy_routes_judgment_to_new_model(ctx: dict) -> None:
    ctx["policy"]["lanes"]["judgment"] = {"model": "newvendor/newmodel-1"}


@when("the router config is built from that policy document")
def build_config_from_policy(ctx: dict) -> None:
    try:
        ctx["config"] = RouterConfig.from_policy_dict(ctx["policy"])
        ctx["config_error"] = None
    except ValueError as exc:
        ctx["config"] = None
        ctx["config_error"] = str(exc)


@then("the config is refused because a shipped model_families entry was redefined")
def refused_for_redefined_family(ctx: dict) -> None:
    assert ctx["config"] is None
    assert ctx["config_error"] is not None
    assert "redefines the shipped entry" in ctx["config_error"]


@then("the refusal names minimax/minimax-01, minimax and not-minimax")
def refusal_names_both_values(ctx: dict) -> None:
    assert "minimax/minimax-01" in ctx["config_error"]
    assert "'minimax'" in ctx["config_error"]
    assert "'not-minimax'" in ctx["config_error"]


@then("the config is accepted")
def config_accepted(ctx: dict) -> None:
    assert ctx["config_error"] is None
    assert ctx["config"] is not None


@then("the judgment lane's family derives to the brand-new family")
def judgment_family_is_new(ctx: dict) -> None:
    assert ctx["config"].lane_family("judgment") == "newvendor"
