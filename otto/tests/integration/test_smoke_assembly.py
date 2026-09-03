"""Assembly smoke: the six Otto v1 lane packages compose in one process.

One end-to-end walk, import-level and in-memory only (no NATS, no
Postgres): a task envelope minted by ``otto.spine`` flows through the
``otto.gateway`` registry check and executes a tool, the work is claimed
in a ``otto.verify`` claim envelope, a verdict signed by the verifier
passes the completion gate, the ``otto.router`` contract renders the
result VERIFIED for Telegram, and a fact carrying the task's provenance
round-trips through the ``otto.memory`` model layer. ``otto.evals``
imports alongside the rest (its runner needs no infra to load).

W2 wiring (cp6, ``otto.obs``): a second test boots every lane package
through its ``boot()`` entrypoint under ``OTTO_OBS_MODE=test``, emits one
task span per component carrying the same task ULID, and proves the
``otto-obs-coverage`` gate green over all six — nothing boots dark.

Store hygiene: every test here runs between ``_reset_shared_store()``
calls, and a third test proves the invariant that this file's teardown
leaves the process-shared exporter usable for whatever runs next.

This is glue proof, not lane proof — each lane's own suite (cp0..cp6)
carries the behavioural coverage.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

# Import the sixth lane at module level so a collection of this file alone
# proves all six packages load in one interpreter.
from otto.evals import models as evals_models
from otto.gateway.core import Envelope as GatewayEnvelope
from otto.gateway.core import ToolGateway
from otto.gateway.registry import ToolRegistry, ToolSpec
from otto.memory.models import Fact, Provenance
from otto.obs.config import MODE_ENV, MODE_TEST
from otto.obs.core import COMPONENT_ATTR, TaskContext
from otto.obs.coverage import check_coverage
from otto.obs.export import obs_test_store
from otto.obs.ulid import ulid_to_trace_id
from otto.router.contract import VerificationStatus, normalise_provider_output
from otto.router.render import UNVERIFIED_PREFIX, render_claims
from otto.spine.envelope import TaskClass, TaskEnvelope, TaskSource, Tier
from otto.surface.envelope import Capability, SurfaceEnvelope, TrustClass
from otto.verify.bus import RecordingBus
from otto.verify.identity import VerifierIdentity
from otto.verify.ledger import CompletionGate, Task, TaskState
from otto.verify.ledger import Tier as LedgerTier
from otto.verify.model import Claim as WorkClaim
from otto.verify.model import ClaimEnvelope
from otto.verify.store import InMemoryVerdictStore
from otto.verify.verifier import CODE_PASSES_TESTS, RerunResult, Verifier

#: The one customer these scenarios speak for. Named once so a reader
#: can see at a glance that every envelope below is tenant-scoped.
TENANT_UNDER_TEST = "tenant-under-test"


def _reset_shared_store() -> None:
    """Clear the shared obs store AND replace its span exporter.

    Same idiom as ``otto/tests/onboard/conftest.py``: ``handle.shutdown()``
    stops the delegate exporter, and a stopped ``InMemorySpanExporter``
    refuses every later export while ``clear()`` does not revive it. Left
    in place, a suite running after this file in the same process would
    export into a dead sink SILENTLY and the coverage gate would read a
    false red. A fresh exporter on both sides of every test removes the
    leak in both directions."""
    store = obs_test_store()
    store.clear()
    store.span_exporter = InMemorySpanExporter()


@pytest.fixture(autouse=True)
def _fresh_shared_store():
    """Every test in this file starts and ends with a live shared store."""
    _reset_shared_store()
    yield
    _reset_shared_store()


class _GreenSandbox:
    """A sandbox runner whose fresh re-run matches the builder's claim."""

    def rerun(self, ref: str) -> RerunResult:
        return RerunResult(exit_code=0, junit_sha256="sha-junit-smoke")


def test_six_lanes_compose_end_to_end() -> None:
    # -- otto.evals: the sixth lane is present and loads -------------------
    assert evals_models is not None

    # -- otto.surface: an inbound event mints the correlation ULID ---------
    surface_env = SurfaceEnvelope(
        tenant_id=TENANT_UNDER_TEST,
        surface="telegram",
        principal="founder",
        trust_class=TrustClass.OPERATOR,
        capabilities=frozenset({Capability.TEXT}),
        content="integration smoke: run the demo tool and verify the work",
        received_at=datetime.now(timezone.utc),
    )
    assert surface_env.is_instruction_bearing

    # -- otto.spine: the surface correlation_id IS the task ULID (W2) ------
    task_env = TaskEnvelope(
        task_id=surface_env.correlation_id,
        tenant_id=surface_env.tenant_id,
        source=TaskSource.telegram,
        **{"class": TaskClass.code},
        input=surface_env.content,
        authority_ceiling=Tier.T2,
        context_budget_tokens=24_000,
        cost_budget_usd=0.50,
        deadline_s=600,
        created_at=surface_env.received_at,
        provenance=f"surface:{surface_env.surface} crew#768 integration smoke",
    )
    assert task_env.task_id == surface_env.correlation_id
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
        signer=Ed25519PrivateKey.generate(),
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
    assert render_claims(router_resp) == [f"{UNVERIFIED_PREFIX}echo tool executed"]
    # Only the completion gate's signed decision upgrades the rendering.
    assert decision.completed
    verified_resp = dataclasses.replace(
        router_resp, verification=VerificationStatus.VERIFIED
    )
    assert render_claims(verified_resp) == ["echo tool executed"]

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


class _StoreBackend:
    """Binds the coverage TraceBackend Protocol to the in-memory store."""

    def span_count(self, component: str, window_seconds: float) -> int:
        return sum(
            1
            for span in obs_test_store().finished_spans()
            if span.resource.attributes.get(COMPONENT_ATTR) == component
        )


def test_w2_every_package_boots_instrumented(monkeypatch) -> None:
    """W2: each package's boot() instruments through otto.obs; the
    coverage gate sees every component; the task ULID is the trace id."""
    monkeypatch.setenv(MODE_ENV, MODE_TEST)

    import otto.evals
    import otto.gateway
    import otto.memory
    import otto.router
    import otto.spine
    import otto.verify

    packages = {
        "spine": otto.spine,
        "evals": otto.evals,
        "gateway": otto.gateway,
        "verify": otto.verify,
        "router": otto.router,
        "memory": otto.memory,
    }
    # W2: the surface envelope's correlation ULID is the task identity the
    # whole trace runs under — surface -> spine -> every component's spans.
    surface_env = SurfaceEnvelope(
        tenant_id=TENANT_UNDER_TEST,
        surface="telegram",
        principal="founder",
        trust_class=TrustClass.OPERATOR,
        capabilities=frozenset({Capability.TEXT}),
        content="obs smoke",
        received_at=datetime.now(timezone.utc),
    )
    ctx = TaskContext(
        task_ulid=surface_env.correlation_id, tenant_id=surface_env.tenant_id
    )
    handles = {name: pkg.boot() for name, pkg in packages.items()}
    try:
        for name, handle in handles.items():
            assert handle.component == name
            with handle.task_span(ctx, f"{name}.smoke"):
                pass

        spans = obs_test_store().finished_spans()
        assert len(spans) == len(packages)
        # ULID doubles as the OpenTelemetry trace id (spec section 3).
        expected_trace_id = ulid_to_trace_id(ctx.task_ulid)
        assert {span.context.trace_id for span in spans} == {expected_trace_id}

        report = check_coverage(sorted(packages), _StoreBackend())
        assert not report.red, report.as_dict()
    finally:
        # Providers are shut down properly; this stops the shared store's
        # current span exporter too, which is exactly why the autouse
        # ``_fresh_shared_store`` fixture replaces it after every test.
        for handle in handles.values():
            handle.shutdown()


def test_regression_fresh_export_lands_after_smoke_teardown(monkeypatch) -> None:
    """Regression (crew#768 integration defect): the smoke teardown above
    must leave the process-shared exporter usable.

    The defect class: ``handle.shutdown()`` on handles booted against the
    shared ``obs_test_store()`` stops the shared ``InMemorySpanExporter``;
    ``clear()`` does not revive it; every later export in the process then
    fails SILENTLY and coverage reads a false red. This test runs after
    ``test_w2_every_package_boots_instrumented`` (pytest keeps definition
    order within a module) and proves the invariant directly: a fresh
    handle booted now, in the same process, still lands a span in the
    shared store, and its export health stays green."""
    monkeypatch.setenv(MODE_ENV, MODE_TEST)

    import otto.spine

    surface_env = SurfaceEnvelope(
        tenant_id=TENANT_UNDER_TEST,
        surface="telegram",
        principal="founder",
        trust_class=TrustClass.OPERATOR,
        capabilities=frozenset({Capability.TEXT}),
        content="post-teardown export probe",
        received_at=datetime.now(timezone.utc),
    )
    ctx = TaskContext(
        task_ulid=surface_env.correlation_id, tenant_id=surface_env.tenant_id
    )
    probe = otto.spine.boot()  # default config: the shared store's exporter
    try:
        with probe.task_span(ctx, "regression.fresh-export"):
            pass
        landed = [
            span
            for span in obs_test_store().finished_spans()
            if span.name == "regression.fresh-export"
        ]
        assert landed, (
            "shared exporter is stopped: a prior test's shutdown poisoned it "
            "and clear() did not revive it"
        )
        assert probe.health.healthy, probe.health.as_dict()
    finally:
        probe.shutdown()  # the autouse fixture replaces the exporter after
