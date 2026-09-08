"""Step definitions for ``features/cp2_gateway_core.feature``.

Verifies the CP2 tool-gateway core (crew#768): schema enforcement, tier
enforcement, the P5 taint cap, the 12-tool cap, total audit coverage, and
the T3 human-gate hook (fail closed).
"""

from __future__ import annotations

import copy
import uuid

from pytest_bdd import given, parsers, scenarios, then, when

from otto.gateway import (
    ApprovalToken,
    Envelope,
    GatewayConfig,
    InMemoryAuditEmitter,
    Tier,
    ToolCapacityExceeded,
    ToolGateway,
    ToolRegistry,
    ToolSpec,
)

scenarios("../features/cp2_gateway_core.feature")

# -- fixed schemas + valid payloads for the tool names this feature uses ----

_SCHEMAS: dict[str, dict] = {
    "fs_write": {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["path", "content"],
        "additionalProperties": False,
    },
    "calendar_ops": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["create", "update", "delete"]},
            "event": {"type": "object"},
        },
        "required": ["action", "event"],
        "additionalProperties": False,
    },
    "email_send": {
        "type": "object",
        "properties": {
            "to": {"type": "string"},
            "subject": {"type": "string"},
            "body": {"type": "string"},
        },
        "required": ["to", "subject", "body"],
        "additionalProperties": False,
    },
}

_VALID_PAYLOADS: dict[str, dict] = {
    "fs_write": {"path": "workspace/notes.md", "content": "hello"},
    "calendar_ops": {"action": "create", "event": {"title": "sync"}},
    "email_send": {"to": "chidi@example.com", "subject": "hi", "body": "hi"},
}


def _register(ctx: dict, name: str, tier: Tier) -> None:
    ctx["registry"].register(
        ToolSpec(
            name=name,
            tier=tier,
            input_schema=_SCHEMAS[name],
            handler=lambda args: {"ok": True, "echo": args},
        )
    )


# -- Background ---------------------------------------------------------------


@given(parsers.parse("a fresh tool registry with a cap of {cap:d} tools"))
def _fresh_registry(ctx: dict, cap: int) -> None:
    ctx["registry"] = ToolRegistry(config=GatewayConfig(max_tools=cap))


@given("a fresh in-memory audit emitter")
def _fresh_emitter(ctx: dict) -> None:
    ctx["emitter"] = InMemoryAuditEmitter()


@given("a gateway built from that registry and that emitter")
def _build_gateway(ctx: dict) -> None:
    ctx["gateway"] = ToolGateway(registry=ctx["registry"], audit_emitter=ctx["emitter"])


# -- tool + envelope setup ------------------------------------------------


@given(
    parsers.parse(
        'a T{tier_digit:d} tool "{name}" registered with a strict input schema'
    )
)
def _tool_registered(ctx: dict, tier_digit: int, name: str) -> None:
    _register(ctx, name, Tier.parse(f"T{tier_digit}"))


@given(
    parsers.parse(
        "a task envelope with authority_ceiling T{ceiling:d} and no untrusted context"
    )
)
def _envelope_trusted(ctx: dict, ceiling: int) -> None:
    ctx["envelope"] = Envelope(
        task_id="task-cp2-core",
        authority_ceiling=Tier.parse(f"T{ceiling}"),
        untrusted=False,
    )


@given(
    parsers.parse(
        "a task envelope with authority_ceiling T{ceiling:d} and untrusted context from web_fetch"
    )
)
def _envelope_untrusted(ctx: dict, ceiling: int) -> None:
    ctx["envelope"] = Envelope(
        task_id="task-cp2-core",
        authority_ceiling=Tier.parse(f"T{ceiling}"),
        untrusted=True,
    )


@given("a registry already holding 12 registered tools")
def _registry_at_cap(ctx: dict) -> None:
    for i in range(12):
        ctx["registry"].register(
            ToolSpec(
                name=f"filler_tool_{i}",
                tier=Tier.T0,
                input_schema={"type": "object", "additionalProperties": True},
                handler=lambda args: {},
            )
        )


@given("no human-gate hook is wired")
def _no_human_gate(ctx: dict) -> None:
    ctx["gateway"].human_gate = None


@given("a human-gate hook that always declines")
def _human_gate_declines(ctx: dict) -> None:
    ctx["gateway"].human_gate = lambda envelope, tool: None


@given("a human-gate hook that always approves")
def _human_gate_approves(ctx: dict) -> None:
    # not a credential: a test double for the approval-card token, ruff's
    # hardcoded-password heuristic fires on the field name "token" alone.
    test_approval_token = "approval-" + uuid.uuid4().hex[:8]
    ctx["gateway"].human_gate = lambda envelope, tool: ApprovalToken(
        token=test_approval_token, approved_by="chidi"
    )


# -- When ----------------------------------------------------------------


@when(parsers.parse('it calls "{name}" with a valid schema payload'))
def _call_valid(ctx: dict, name: str) -> None:
    ctx["response"] = ctx["gateway"].call(ctx["envelope"], name, _VALID_PAYLOADS[name])


@when(parsers.parse('it calls "{name}" with a payload missing a required field'))
def _call_invalid(ctx: dict, name: str) -> None:
    payload = copy.deepcopy(_VALID_PAYLOADS[name])
    required_key = _SCHEMAS[name]["required"][0]
    del payload[required_key]
    ctx["response"] = ctx["gateway"].call(ctx["envelope"], name, payload)


@when("a 13th tool is registered")
def _register_13th(ctx: dict) -> None:
    try:
        ctx["registry"].register(
            ToolSpec(
                name="one_tool_too_many",
                tier=Tier.T0,
                input_schema={"type": "object", "additionalProperties": True},
                handler=lambda args: {},
            )
        )
        ctx["exception"] = None
    except ToolCapacityExceeded as exc:
        ctx["exception"] = exc


# -- Then ------------------------------------------------------------------


@then("the call executes")
def _call_executed(ctx: dict) -> None:
    response = ctx["response"]
    assert response.ok, f"expected the call to execute, got denial: {response.denial}"


@then(parsers.parse('the call is denied with reason "{reason}"'))
def _call_denied(ctx: dict, reason: str) -> None:
    response = ctx["response"]
    assert response.denied, "expected the call to be denied, it executed"
    assert response.denial is not None
    assert response.denial.reason.value == reason, (
        f"expected denial reason {reason!r}, got {response.denial.reason.value!r}: "
        f"{response.denial.message}"
    )


@then(parsers.parse('the denial\'s effective tier is "{tier}"'))
def _denial_effective_tier(ctx: dict, tier: str) -> None:
    assert ctx["response"].denial.effective_tier == tier


@then(parsers.parse('the denial\'s requested tier is "{tier}"'))
def _denial_requested_tier(ctx: dict, tier: str) -> None:
    assert ctx["response"].denial.requested_tier == tier


@then(parsers.parse("exactly {n:d} audit event has been emitted"))
def _audit_count(ctx: dict, n: int) -> None:
    assert len(ctx["emitter"].events) == n, ctx["emitter"].events


@then(
    parsers.parse('the last audit event records tool "{name}", decision "{decision}"')
)
def _audit_last_event(ctx: dict, name: str, decision: str) -> None:
    last = ctx["emitter"].events[-1]
    assert last.tool_name == name
    assert last.decision == decision


@then("registration is refused with ToolCapacityExceeded")
def _registration_refused(ctx: dict) -> None:
    assert isinstance(ctx["exception"], ToolCapacityExceeded), ctx.get("exception")


@then(parsers.parse("the registry still holds exactly {n:d} tools"))
def _registry_size(ctx: dict, n: int) -> None:
    assert len(ctx["registry"]) == n
