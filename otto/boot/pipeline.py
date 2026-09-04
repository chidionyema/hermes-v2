"""The one place an inbound Telegram update crosses the platform lanes.

Same shape as ``otto/tests/integration/test_smoke_assembly.py``'s
``test_six_lanes_compose_end_to_end``, run against a real inbound event
instead of a synthetic one: surface normalises, spine mints the task
envelope, the gateway checks tool authority under the taint rule, the
router normalises a response and marks it unverified (this boot lane
never calls the Verification Plane — P1 holds by omission, not by
claim), and a fact carrying the task's own provenance is built the same
way ``otto.memory.models.Fact`` proves it round-trips in that same test.

The model step is live. ``otto.router.core.Router`` executes the task
against the estate model router through ``LiteLLMClient``, under the
lane policy, the budget ledger and the bounded retries the router
already enforces. Before this, the step was a canned payload and every
reply the founder received read ``unverified: noted: <his own words>``,
which is an echo, not an answer. He reported that three times.

One honest gap remains, named here rather than hidden: the memory fact
is constructed and round-tripped through ``Fact.to_row``/``Fact.from_row``
(proving the shape is correct) but is not written to the real Postgres
store — ``otto.memory.store`` needs a live database connection this boot
lane has no contract for yet (no env var for it was named in this task).

The reply is still marked unverified, and that is correct: the
Verification Plane is not called here, so P1 holds by omission. An
unverified answer from a real model is the honest state; an unverified
echo of the question was not an answer at all.

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
from otto.router.budget import BudgetLedger
from otto.router.config import RouterConfig
from otto.router.contract import RouterResponse, normalise_provider_output
from otto.router.core import InMemoryNotifier, OutcomeState, Router, RouterTask
from otto.router.providers import LiteLLMClient, ProviderClient
from otto.router.render import render_claim, render_claims
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


#: One Router for the process. The config, ledger and notifier are all
#: plain dataclasses with working defaults, so this needs no wiring beyond
#: the lane policy the deployment already sets in the environment.
_ROUTER: Router | None = None


def _router() -> Router:
    global _ROUTER
    if _ROUTER is None:
        config = RouterConfig()
        _ROUTER = Router(
            config=config,
            ledger=BudgetLedger(config=config),
            notifier=InMemoryNotifier(),
        )
    return _ROUTER


#: The router's contract (otto/router/contract.py) parses the provider's
#: output as this JSON object, so the prompt has to ask for exactly it.
#: Asking in the prompt rather than post-processing keeps the one parser
#: the only thing that decides whether output is well formed.
_CONTRACT_PROMPT = """You are Otto, the operator's assistant for this estate.

You may think for as long as you need to, but your thinking is not the
reply. Your entire output must be one raw JSON object: no markdown fence,
no preamble, no commentary before it or after it. A reasoning lane that
narrates its way to the answer breaks the parser exactly as badly as an
answer cut off half way through (founder, 2026-09-04).

Answer the message below. Reply with a single JSON object and nothing else:

{{"answer": "<your answer in plain English>",
  "claims": [{{"text": "<one factual claim>", "evidence_refs": [], "confidence": "high|med|low"}}],
  "proposed_actions": [],
  "unknowns": ["<anything you could not establish>"]}}

Put every factual statement in "claims" as well as in "answer". If you are
not sure of something, say so in "unknowns" rather than asserting it.

Message:
{message}"""


def _prompt_for(message: str) -> str:
    return _CONTRACT_PROMPT.format(message=message)


#: Typing one of these first sends the message to the reasoning lane
#: instead of the lane the route table would have picked.
#:
#: The route table (otto/router/config.py) decides by task attributes, and
#: nothing inbound from Telegram distinguishes "answer this quickly" from
#: "think hard about this" -- every message arrives as class `research`.
#: Until something upstream can tell those apart, the operator says which
#: he wants, which is deterministic and costs no extra model call. The
#: prefix is stripped before the message reaches the model, so the model
#: never sees the routing instruction as part of the question.
DEEP_PREFIXES = ("/think", "/kimi")

#: The task class the route table maps to the deep lane.
DEEP_TASK_CLASS = "deep"


def route_hint(content: str) -> tuple[str, str]:
    """Split an inbound message into (task_class, message).

    Returns the deep task class and the message with the prefix removed
    when the operator asked for the reasoning lane; otherwise the default
    research class and the message untouched. A prefix must be a whole
    word: `/thinking about lunch` is a question, not a routing request.
    """
    stripped = content.strip()
    for prefix in DEEP_PREFIXES:
        if stripped == prefix:
            return DEEP_TASK_CLASS, ""
        if stripped.startswith(prefix) and stripped[len(prefix)] in " \n\t":
            return DEEP_TASK_CLASS, stripped[len(prefix) :].strip()
    return "research", stripped


def _state_sentence(outcome) -> str:
    """Plain English for a router state that is not a completed answer."""
    sentences = {
        OutcomeState.QUEUED_BUDGET: "I have not answered: today's budget for this lane is spent.",
        OutcomeState.PAUSED_TASK_BUDGET: "I have not answered: this one task ran past its own budget.",
        OutcomeState.NEEDS_HUMAN: "I could not reach the model, so I have not answered.",
        OutcomeState.REFUSED_MALFORMED: "The model replied in a shape I refuse to parse, so I have not answered.",
    }
    base = sentences.get(outcome.state, "I have not answered.")
    return f"{base} ({outcome.reason})" if outcome.reason else base


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


def extract_chat_id(native_event: dict) -> int | None:
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
    provider_client: ProviderClient | None = None,
) -> PipelineOutcome:
    """Run one inbound Telegram update across every lane. Never raises
    for a well-formed but untrusted or empty event — the caller (the
    HTTP layer) validates that ``native_event`` is at least dict-shaped
    before this function is ever called."""
    surface_env = binding.normalize(native_event, tenant_id=LEGACY_SINGLE_TENANT)
    ctx = TaskContext(
        task_ulid=surface_env.correlation_id, tenant_id=surface_env.tenant_id
    )
    chat_id = extract_chat_id(native_event)

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
    task_class, asked = route_hint(noted_text)
    with obs.router.task_span(ctx, "router.execute"):
        outcome = _router().execute(
            RouterTask(
                input=_prompt_for(asked or noted_text),
                source="telegram",
                task_class=task_class,
                task_id=task_env.task_id,
            ),
            provider_client or LiteLLMClient(),
        )
        obs.router.info(
            "router.outcome",
            ctx,
            state=outcome.state.value,
            lane=outcome.lane,
            attempts=outcome.attempts,
        )
        router_resp = outcome.response
        if (
            outcome.state is not OutcomeState.COMPLETED_UNVERIFIED
            or router_resp is None
        ):
            # The router refused, queued or paused the task. Every one of
            # those states is named, and the sender is told which one in
            # plain words rather than receiving a manufactured answer.
            router_resp = normalise_provider_output(
                json.dumps(
                    {
                        "answer": _state_sentence(outcome),
                        "claims": [
                            {
                                "text": _state_sentence(outcome),
                                "evidence_refs": [],
                                "confidence": "low",
                            }
                        ],
                        "proposed_actions": [],
                        "unknowns": [outcome.reason or outcome.state.value],
                    }
                ),
                lane=outcome.lane,
                model="none (router did not complete)",
                task_id=task_env.task_id,
                cost_usd=outcome.charged_usd,
                tokens=0,
            )
        # P1 holds by construction: normalise_provider_output always mints
        # UNVERIFIED (otto/router/contract.py), and this pipeline never
        # calls the Verification Plane, so render_claims
        # always applies the unverified marker below.
        reply_lines = render_claims(router_resp)
        if not reply_lines and router_resp.answer:
            # render_claims renders claims, not the answer. A model that
            # answers well but lists no claims would otherwise send silence,
            # which reads exactly like the bot being down.
            reply_lines = [
                render_claim(router_resp.answer, has_evidence=False, verified=False)
            ]

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
