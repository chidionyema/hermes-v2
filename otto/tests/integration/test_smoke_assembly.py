"""Assembly smoke: the six Otto v1 lane packages compose in one process.

One end-to-end walk, import-level and in-memory only (no NATS, no
Postgres): a task envelope minted by ``otto.spine`` flows through the
``otto.gateway`` registry check and executes a tool, the work is claimed
in a ``otto.verify`` claim envelope, a verdict signed by the verifier
passes the completion gate, the ``otto.router`` contract renders the
result VERIFIED for Telegram, and a fact carrying the task's provenance
round-trips through the ``otto.memory`` model layer. ``otto.evals``
imports alongside the rest (its runner needs no infra to load).

This is glue proof, not lane proof — each lane's own suite (cp0..cp5)
carries the behavioural coverage.
"""

from __future__ import annotations

import dataclasses
import json

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

# Import the sixth lane at module level so a collection of this file alone
# proves all six packages load in one interpreter.
from otto.evals import models as evals_models
from otto.gateway.core import Envelope as GatewayEnvelope
from otto.gateway.core import ToolGateway
from otto.gateway.registry import ToolRegistry, ToolSpec
from otto.memory.models import Fact, Provenance
from otto.router.contract import VerificationStatus, normalise_provider_output
from otto.router.render import UNVERIFIED_PREFIX, render_claims_for_telegram
from otto.spine.envelope import TaskClass, TaskEnvelope, TaskSource, Tier
from otto.verify.bus import RecordingBus
from otto.verify.identity import VerifierIdentity
from otto.verify.ledger import CompletionGate, Task, TaskState
from otto.verify.ledger import Tier as LedgerTier
from otto.verify.model import Claim as WorkClaim
from otto.verify.model import ClaimEnvelope
from otto.verify.store import InMemoryVerdictStore
from otto.verify.verifier import CODE_PASSES_TESTS, RerunResult, Verifier


class _GreenSandbox:
    """A sandbox runner whose fresh re-run matches the builder's claim."""

    def rerun(self, ref: str) -> RerunResult:
        return RerunResult(exit_code=0, junit_sha256="sha-junit-smoke")


def test_six_lanes_compose_end_to_end() -> None:
    # -- otto.evals: the sixth lane is present and loads -------------------
    assert evals_models is not None

    # -- otto.spine: mint the task envelope --------------------------------
    task_env = TaskEnvelope.new(
        source=TaskSource.api,
        task_class=TaskClass.code,
        input="integration smoke: run the demo tool and verify the work",
        authority_ceiling=Tier.T2,
        provenance="crew#768 integration smoke",
    )
    assert task_env.effective_tier is Tier.T2  # untainted, ceiling holds

    # -- otto.gateway: registry check and tool execution -------------------
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="echo",
            tier="T1",
            input_schema={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
                "additionalProperties": False,
            },
            handler=lambda args: {"echoed": args["text"]},
        )
    )
    gateway = ToolGateway(registry=registry)
    gw_env = GatewayEnvelope(
        task_id=task_env.task_id,
        authority_ceiling=task_env.effective_tier.value,
        untrusted=task_env.is_taint_capped,
    )
    response = gateway.call(gw_env, "echo", {"text": "smoke"})
    assert response.ok and response.output == {"echoed": "smoke"}
    # The registry check itself: an unregistered tool is denied, not raised.
    assert gateway.call(gw_env, "no-such-tool", {}).denied

    # -- otto.verify: claim the work, sign a verdict, complete the task ----
    claim_env = ClaimEnvelope(
        task_id=task_env.task_id,
        builder_identity="builder-lane",
        claims=(
            WorkClaim(
                claim_type=CODE_PASSES_TESTS,
                claimed={"exit_code": 0, "junit_sha256": "sha-junit-smoke"},
                evidence_spec={"ref": "otto/v1-integration"},
            ),
        ),
    )
    identity = VerifierIdentity(
        name="verification-plane",
        key_id="vp-smoke",
        private_key=Ed25519PrivateKey.generate(),
    )
    verifier = Verifier(identity, sandbox_runner=_GreenSandbox())
    gate = CompletionGate(
        trusted_keys={"vp-smoke": identity.public_key_bytes()},
        store=InMemoryVerdictStore(),
        bus=RecordingBus(),
        clock=lambda: 0.0,
    )
    task = Task(
        task_id=task_env.task_id,
        authority_ceiling=LedgerTier(task_env.effective_tier.value),
        deadline_s=float(task_env.deadline_s),
        created_at=0.0,
    )
    nonce = gate.await_verdict(task, claim_env)
    verdict = verifier.issue_verdict(claim_env, nonce)
    assert verdict.result == "pass" and verdict.hardness == "hard"
    decision = gate.submit_verdict(task_env.task_id, verdict)
    assert decision.completed and decision.task_state is TaskState.COMPLETED

    # -- otto.router: contract normalises, verdict flips render to clean ---
    provider_text = json.dumps(
        {
            "answer": "the demo tool ran and the work was verified",
            "claims": [
                {
                    "text": "echo tool executed",
                    "evidence_refs": [f"verdict:{verdict.verdict_id}"],
                    "confidence": "high",
                }
            ],
            "proposed_actions": [],
            "unknowns": [],
        }
    )
    router_resp = normalise_provider_output(
        provider_text,
        lane="raw-execution",
        model="smoke-model",
        task_id=task_env.task_id,
        cost_usd=0.0,
        tokens=42,
    )
    # P1 holds: the router alone never verifies.
    assert router_resp.verification is VerificationStatus.UNVERIFIED
    assert render_claims_for_telegram(router_resp) == [
        f"{UNVERIFIED_PREFIX}echo tool executed"
    ]
    # Only the completion gate's signed decision upgrades the rendering.
    assert decision.completed
    verified_resp = dataclasses.replace(
        router_resp, verification=VerificationStatus.VERIFIED
    )
    assert render_claims_for_telegram(verified_resp) == ["echo tool executed"]

    # -- otto.memory: a fact with the task's provenance round-trips --------
    fact = Fact(
        content="integration smoke completed with a signed verdict",
        provenance=Provenance(
            source_envelope_ulid=task_env.task_id,
            tier_at_capture=task_env.effective_tier.value,
            taint=task_env.is_taint_capped,
        ),
        entity="otto/v1-integration",
        attribute="smoke",
        value=verdict.verdict_id,
    )
    restored = Fact.from_row(fact.to_row())
    assert restored.provenance.source_envelope_ulid == task_env.task_id
    assert restored.provenance.tier_at_capture == "T2"
    assert restored.value == verdict.verdict_id
