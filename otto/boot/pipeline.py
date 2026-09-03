"""The one place an inbound Telegram update crosses the platform lanes.

Same shape as ``otto/tests/integration/test_smoke_assembly.py``'s
``test_six_lanes_compose_end_to_end``, run against a real inbound event
instead of a synthetic one: surface normalises, spine mints the task
envelope, the gateway checks tool authority under the taint rule, the
router normalises a response and marks it unverified (this boot lane
never calls the Verification Plane — P1 holds by omission, not by
claim), and a fact carrying the task's own provenance is built the same
way ``otto.memory.models.Fact`` proves it round-trips in that same test.

Two things this boot lane deliberately does NOT do yet, both honest
gaps named here rather than hidden:

* it does not call a real model through ``otto.router.core.Router`` —
  there is no ``ProviderClient`` wired to a live provider in this
  checkpoint, so the "model" step is the same deterministic,
  structurally-shaped stand-in the smoke test uses (a canned
  ``normalise_provider_output`` payload), not a live completion;
* the memory fact is constructed and round-tripped through
  ``Fact.to_row``/``Fact.from_row`` (proving the shape is correct) but
  is not written to the real Postgres store — ``otto.memory.store``
  needs a live database connection this boot lane has no contract for
  yet (no env var for it was named in this task).

Security posture (P5, the two-source rule): an unrecognised chat id
normalises to ``TrustClass.UNTRUSTED``. Its task envelope still crosses
every lane — that is what proves the taint cap actually holds under a
real message, not just in a unit test — but its authority is capped to
T1 by ``TaskEnvelope.effective_tier`` (spec section 10.2) while the
gateway's one tool sits at T2, so the gateway denies the call every
time. When the gateway denies, this pipeline sends no reply at all:
an unrecognised sender gets silence, not a hint about what would have
happened, and no message ever reaches ``TelegramTransport.send_message``
for it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from otto.boot.transport import TelegramTransport
from otto.gateway.core import Envelope as GatewayEnvelope
from otto.gateway.core import GatewayResponse, ToolGateway
from otto.gateway.registry import ToolRegistry, ToolSpec
from otto.gateway.registry import Tier as GatewayTier
from otto.memory.models import Fact, Provenance
from otto.obs.core import ObsHandle, TaskContext
from otto.router.contract import RouterResponse, normalise_provider_output
from otto.router.render import render_claims
from otto.spine.envelope import TaskClass, TaskEnvelope, TaskSource, Tier, TrustTag
from otto.surface.bindings.telegram import TelegramBinding
from otto.surface.envelope import SurfaceEnvelope, TrustClass

#: The one tool this boot lane registers. Tier T2 so an untrusted sender
#: (capped to T1 by the taint rule) can never reach it — the pipeline's
#: proof that "no tool authority" is a gateway decision, not a hope.
NOTE_TOOL_NAME = "note"
_NOTE_TIER = GatewayTier.T2

#: The one customer this legacy single-channel boot lane serves.
#:
#: This lane predates the Universal Event Gateway (``otto/ingress``) and
#: is the only Telegram-shaped door left in the codebase. It is kept
#: because it is the door currently answering in the cluster, not because
#: it is the pattern to repeat: the gateway resolves the tenant from the
#: presented credential per request and serves every customer from one
#: process, which is what this constant cannot do.
#:
#: Deliberately a constant here rather than an environment variable on
#: the pod: the founder's 2026-09-03 directive forbids per-customer
#: configuration reaching a deployment, and a value hard-wired in one
#: named place is far easier to delete than a value spread across
#: manifests. Deleting this constant is the last step of the migration.
LEGACY_SINGLE_TENANT = "legacy-boot-lane"

_NOTE_SCHEMA = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
    "additionalProperties": False,
}


def _note_handler(args: dict) -> dict:
    return {"noted": args["text"]}


def build_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name=NOTE_TOOL_NAME,
            tier=_NOTE_TIER,
            input_schema=_NOTE_SCHEMA,
            handler=_note_handler,
        )
    )
    return registry


@dataclass(frozen=True)
class ObsHandles:
    """One ``ObsHandle`` per lane this pipeline touches, booted once at
    process start and reused for every request (W2 wiring, LAW 50)."""

    boot: ObsHandle
    spine: ObsHandle
    gateway: ObsHandle
    router: ObsHandle
    memory: ObsHandle


def boot_obs_handles() -> ObsHandles:
    """Instrument every lane this pipeline crosses, or refuse to run dark.

    Deferred imports: each package's own ``boot()`` is the contract
    (``otto.spine.boot``, ``otto.gateway.boot``, ...), imported here
    rather than at module load so a test that only wants
    ``otto.boot.pipeline`` for its pure functions never has to satisfy
    every lane's own import graph up front."""
    from otto.obs import instrument as instrument_boot

    import otto.gateway as gateway_pkg
    import otto.memory as memory_pkg
    import otto.router as router_pkg
    import otto.spine as spine_pkg

    return ObsHandles(
        boot=instrument_boot("boot"),
        spine=spine_pkg.boot(),
        gateway=gateway_pkg.boot(),
        router=router_pkg.boot(),
        memory=memory_pkg.boot(),
    )


@dataclass(frozen=True)
class PipelineOutcome:
    """What happened to one inbound update, in full — nothing implied.

    ``reply_chat_id``/``reply_text`` are both set only when a reply is
    actually warranted; a caller sends nothing when either is ``None``.
    """

    surface_envelope: SurfaceEnvelope
    task_envelope: TaskEnvelope | None
    gateway_response: GatewayResponse | None
    router_response: RouterResponse | None
    fact: Fact | None
    reply_chat_id: int | None
    reply_text: str | None


def _extract_chat_id(native_event: dict) -> int | None:
    """The same lookup ``TelegramBinding.normalize`` performs, written
    defensively (``isinstance`` guards throughout) so a malformed but
    dict-shaped update can never raise here — this is the one place the
    pipeline decides where a reply would go, independent of whether the
    binding itself found a trusted principal."""
    message = native_event.get("message", native_event)
    if not isinstance(message, dict):
        return None
    chat = message.get("chat", {})
    if not isinstance(chat, dict):
        return None
    chat_id = chat.get("id")
    return chat_id if isinstance(chat_id, int) else None


def process_update(
    native_event: dict,
    *,
    binding: TelegramBinding,
    registry_gateway: ToolGateway,
    obs: ObsHandles,
) -> PipelineOutcome:
    """Run one inbound Telegram update across every lane. Never raises
    for a well-formed but untrusted or empty event — the caller (the
    HTTP layer) validates that ``native_event`` is at least dict-shaped
    before this function is ever called."""
    surface_env = binding.normalize(native_event, tenant_id=LEGACY_SINGLE_TENANT)
    ctx = TaskContext(
        task_ulid=surface_env.correlation_id, tenant_id=surface_env.tenant_id
    )
    chat_id = _extract_chat_id(native_event)

    with obs.boot.task_span(ctx, "boot.receive"):
        obs.boot.info(
            "webhook.received",
            ctx,
            trust_class=surface_env.trust_class.value,
            has_chat_id=chat_id is not None,
        )

    content = (surface_env.content or "").strip()
    if not surface_env.is_instruction_bearing or not content:
        return PipelineOutcome(surface_env, None, None, None, None, None, None)

    taint = (
        frozenset({TrustTag.untrusted})
        if surface_env.trust_class is TrustClass.UNTRUSTED
        else frozenset()
    )
    with obs.spine.task_span(ctx, "spine.mint_envelope"):
        task_env = TaskEnvelope(
            task_id=surface_env.correlation_id,
            tenant_id=surface_env.tenant_id,
            source=TaskSource.telegram,
            **{"class": TaskClass.comms},
            input=content,
            authority_ceiling=Tier.T2,
            context_budget_tokens=24_000,
            cost_budget_usd=0.50,
            deadline_s=600,
            created_at=surface_env.received_at,
            provenance=f"surface:telegram principal:{surface_env.principal or 'unknown'}",
            taint=taint,
        )

    with obs.gateway.task_span(ctx, "gateway.call"):
        gw_env = GatewayEnvelope(
            task_id=task_env.task_id,
            authority_ceiling=task_env.effective_tier.value,
            untrusted=task_env.is_taint_capped,
        )
        gw_response = registry_gateway.call(gw_env, NOTE_TOOL_NAME, {"text": content})
        if task_env.is_taint_capped:
            obs.gateway.metrics.taint_hit(ctx, source="telegram")

    if gw_response.denied:
        obs.gateway.info(
            "gateway.denied",
            ctx,
            reason=gw_response.denial.reason.value if gw_response.denial else "unknown",
        )
        # No tool authority was granted: no reply is sent, and no router
        # or memory step runs for a call that never executed.
        return PipelineOutcome(
            surface_env, task_env, gw_response, None, None, None, None
        )

    noted_text = gw_response.output["noted"] if gw_response.output else content
    with obs.router.task_span(ctx, "router.normalise"):
        provider_text = json.dumps(
            {
                "answer": f"noted: {noted_text}",
                "claims": [
                    {
                        "text": f"noted: {noted_text}",
                        "evidence_refs": [
                            f"tool:{NOTE_TOOL_NAME}:{gw_response.envelope_id}"
                        ],
                        "confidence": "high",
                    }
                ],
                "proposed_actions": [],
                "unknowns": [],
            }
        )
        router_resp = normalise_provider_output(
            provider_text,
            lane="boot-note",
            model="boot-deterministic-stub",
            task_id=task_env.task_id,
            cost_usd=0.0,
            tokens=0,
        )
        # P1 holds by construction: normalise_provider_output always mints
        # UNVERIFIED (otto/router/contract.py), and this pipeline never
        # calls the Verification Plane, so render_claims
        # always applies the unverified marker below.
        reply_lines = render_claims(router_resp)

    with obs.memory.task_span(ctx, "memory.write_fact"):
        fact = Fact(
            content=content,
            provenance=Provenance(
                source_envelope_ulid=task_env.task_id,
                tier_at_capture=task_env.effective_tier.value,
                taint=task_env.is_taint_capped,
            ),
            entity="otto/boot",
            attribute="telegram-note",
            value=gw_response.envelope_id,
        )
        restored = Fact.from_row(fact.to_row())
        obs.memory.info(
            "memory.fact_round_tripped",
            ctx,
            fact_id=restored.id,
        )

    return PipelineOutcome(
        surface_envelope=surface_env,
        task_envelope=task_env,
        gateway_response=gw_response,
        router_response=router_resp,
        fact=restored,
        reply_chat_id=chat_id,
        reply_text="\n".join(reply_lines) if reply_lines else None,
    )


def deliver(outcome: PipelineOutcome, transport: TelegramTransport) -> bool:
    """Send the reply Telegram is owed, if any. Returns whether a
    message was actually sent (tests assert on this rather than on
    Telegram's own wording)."""
    if outcome.reply_chat_id is None or not outcome.reply_text:
        return False
    transport.send_message(outcome.reply_chat_id, outcome.reply_text)
    return True
