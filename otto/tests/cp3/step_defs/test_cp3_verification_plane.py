"""Step definitions for ``features/cp3_verification_plane.feature``.

Verifies the CP3 Verification Plane (crew#768, spec section 7): a valid
signed verdict is the only path to ``completed``; forged, replayed and
absent verdicts each leave the task un-completable; prover credentials
deny every write at the credential layer; the false-success eval set
leaks nothing; and every network failure fails closed, never silently
pass.
"""

from __future__ import annotations

import hashlib

from pytest_bdd import given, scenarios, then, when

from otto.verify import (
    FAIL,
    PASS,
    PROVER_SYSTEMS,
    SOFT,
    Claim,
    ClaimEnvelope,
    CompletionGate,
    InMemoryVerdictStore,
    ProviderTimeout,
    RecordingBus,
    RerunResult,
    SourceUnreachable,
    Task,
    TaskState,
    Tier,
    Verifier,
    WriteDenied,
    mint_prover_accounts,
    new_trace_id,
    prover_write_deny_report,
    run_false_success_suite,
)

scenarios("../features/cp3_verification_plane.feature")

_BUILDER = "orchestrator"
_ARTIFACT_BYTES = b"the artifact the builder actually produced"
_ARTIFACT_SHA = f"sha256:{hashlib.sha256(_ARTIFACT_BYTES).hexdigest()}"


class TruthfulArtifacts:
    """Independent artifact fetch that returns the real bytes."""

    def fetch(self, locator: str) -> bytes:
        return _ARTIFACT_BYTES


class UnreachableSandbox:
    """Fresh-sandbox rerun whose target ref is unreachable."""

    def rerun(self, ref: str) -> RerunResult:
        raise SourceUnreachable(f"cannot reach ref {ref!r}")


class TimeoutLane:
    """Verify-lane client whose provider times out mid-check."""

    def supports(self, statement: str, context: str) -> bool:
        raise ProviderTimeout("verify lane provider timed out")


class AgreeableLane:
    """Verify-lane client that supports the statement (a soft pass)."""

    def supports(self, statement: str, context: str) -> bool:
        return True


def _artifact_claim() -> Claim:
    return Claim(
        "artifact_produced",
        {"sha256": _ARTIFACT_SHA},
        {"locator": "oci://staging-bucket/artifact.bin"},
    )


def _register_task(
    ctx: dict,
    clock,
    claim: Claim,
    *,
    tier: Tier = Tier.T1,
    key: str = "task",
) -> Task:
    task = Task(
        task_id=new_trace_id(),
        authority_ceiling=tier,
        deadline_s=600.0,
        created_at=clock(),
    )
    envelope = ClaimEnvelope(
        task_id=task.task_id, builder_identity=_BUILDER, claims=(claim,)
    )
    nonce = ctx["gate"].await_verdict(task, envelope)
    ctx[key] = task
    ctx[f"{key}_envelope"] = envelope
    ctx[f"{key}_nonce"] = nonce
    return task


# -- Background ---------------------------------------------------------------


@given("the staging cluster only, zero production credentials in scope")
def _staging_only(ctx: dict, clock, verifier_identity) -> None:
    # The modeled scope names staging systems only; nothing here can name
    # or reach production, and the identity's key is ephemeral, loaded via
    # the env-named path route (no literal key material anywhere).
    ctx["clock"] = clock
    ctx["identity"] = verifier_identity
    ctx["credential_scope"] = {system: "staging" for system in PROVER_SYSTEMS}
    assert all(scope == "staging" for scope in ctx["credential_scope"].values())


@given(
    "the Verification Plane holds its own read-only ServiceAccounts, "
    "distinct from the orchestrator's, sharing nothing but the bus"
)
def _distinct_accounts(ctx: dict, clock, verifier_identity) -> None:
    ctx["prover_accounts"] = mint_prover_accounts(
        owner=verifier_identity.name,
        readers={system: lambda target: "staging-read" for system in PROVER_SYSTEMS},
    )
    orchestrator_accounts = mint_prover_accounts(owner=_BUILDER, readers={})
    assert {a.owner for a in ctx["prover_accounts"].values()} == {
        verifier_identity.name
    }
    assert {a.owner for a in orchestrator_accounts.values()} == {_BUILDER}

    # The bus is the one shared thing; the gate holds public keys only.
    ctx["bus"] = RecordingBus()
    ctx["store"] = InMemoryVerdictStore()
    ctx["gate"] = CompletionGate(
        trusted_keys={verifier_identity.key_id: verifier_identity.public_key_bytes()},
        store=ctx["store"],
        bus=ctx["bus"],
        clock=clock,
    )


# -- Scenario: happy path -----------------------------------------------------


@given("a task at awaiting_verdict with a claim package published")
def _task_with_claim(ctx: dict, clock) -> None:
    _register_task(ctx, clock, _artifact_claim())


@when(
    "the prover verifies the claim by the cheapest deterministic method "
    "and signs a verdict referencing that task's own task_id"
)
def _prover_verifies(ctx: dict) -> None:
    verifier = Verifier(ctx["identity"], artifact_fetcher=TruthfulArtifacts())
    verdict = verifier.issue_verdict(ctx["task_envelope"], ctx["task_nonce"])
    assert verdict.task_id == ctx["task"].task_id
    ctx["verdict"] = verdict
    ctx["decision"] = ctx["gate"].submit_verdict(ctx["task"].task_id, verdict)


@then("the task transitions to completed")
def _task_completed(ctx: dict) -> None:
    assert ctx["decision"].completed is True
    assert ctx["task"].state is TaskState.COMPLETED


@then("the verdict is published to otto.verdict.v1.pass")
def _published_pass(ctx: dict) -> None:
    subjects = [subject for subject, _ in ctx["bus"].published]
    assert "otto.verdict.v1.pass" in subjects


# -- Scenario: forged verdict -------------------------------------------------


@given("a task at awaiting_verdict")
def _plain_task(ctx: dict, clock) -> None:
    _register_task(ctx, clock, _artifact_claim())


@when("a verdict with an invalid Ed25519 signature is published for its task_id")
def _forged_verdict(ctx: dict, rogue_identity) -> None:
    # The forger claims the trusted key id but signs with a different key.
    forger = Verifier(rogue_identity, artifact_fetcher=TruthfulArtifacts())
    verdict = forger.issue_verdict(ctx["task_envelope"], ctx["task_nonce"])
    assert verdict.prover_key_id == ctx["identity"].key_id
    ctx["decision"] = ctx["gate"].submit_verdict(ctx["task"].task_id, verdict)


@then("the task does not transition to completed")
def _not_completed(ctx: dict) -> None:
    assert ctx["decision"].completed is False
    assert ctx["task"].state is not TaskState.COMPLETED


@then("it remains awaiting_verdict or moves to needs_human")
def _awaiting_or_needs_human(ctx: dict) -> None:
    assert ctx["task"].state in (
        TaskState.AWAITING_VERDICT,
        TaskState.NEEDS_HUMAN,
    )


# -- Scenario: replayed verdict -----------------------------------------------


@given("a task A at awaiting_verdict")
def _task_a(ctx: dict, clock) -> None:
    _register_task(ctx, clock, _artifact_claim(), key="task_a")


@given("a validly signed verdict that references task B's task_id, not task A's")
def _verdict_for_task_b(ctx: dict, clock) -> None:
    _register_task(ctx, clock, _artifact_claim(), key="task_b")
    verifier = Verifier(ctx["identity"], artifact_fetcher=TruthfulArtifacts())
    ctx["verdict_b"] = verifier.issue_verdict(
        ctx["task_b_envelope"], ctx["task_b_nonce"]
    )
    assert ctx["verdict_b"].task_id != ctx["task_a"].task_id


@when("that verdict is published on the stream")
def _apply_b_to_a(ctx: dict) -> None:
    ctx["decision"] = ctx["gate"].submit_verdict(
        ctx["task_a"].task_id, ctx["verdict_b"]
    )


@then("task A does not transition to completed")
def _task_a_not_completed(ctx: dict) -> None:
    assert ctx["decision"].completed is False
    assert ctx["task_a"].state is not TaskState.COMPLETED


# -- Scenario: absent verdict -------------------------------------------------


@given("a task at awaiting_verdict with no verdict published within its deadline")
def _task_past_deadline(ctx: dict, clock) -> None:
    task = _register_task(ctx, clock, _artifact_claim())
    clock.advance(task.deadline_s + 1)
    ctx["expired"] = ctx["gate"].expire_overdue()


@then("the task never transitions to completed")
def _never_completed(ctx: dict) -> None:
    assert ctx["task"].state is not TaskState.COMPLETED


@then("it is surfaced as needs_human or failed, never silently abandoned as complete")
def _surfaced_loudly(ctx: dict) -> None:
    assert ctx["task"].task_id in ctx["expired"]
    assert ctx["task"].state in (TaskState.NEEDS_HUMAN, TaskState.FAILED)


# -- Scenario: prover write deny ----------------------------------------------


@given("the prover's read-only ServiceAccounts for k8s, Postgres and Object Storage")
def _prover_accounts(ctx: dict, verifier_identity) -> None:
    ctx["accounts"] = mint_prover_accounts(owner=verifier_identity.name, readers={})
    assert set(ctx["accounts"]) == set(PROVER_SYSTEMS)


@when('an engineer runs "otto test prover-write-deny" once per system')
def _run_write_deny(ctx: dict) -> None:
    ctx["deny_report"] = prover_write_deny_report(ctx["accounts"])


@then(
    "every write attempt is denied by the credential itself, not by application logic"
)
def _every_write_denied(ctx: dict) -> None:
    assert set(ctx["deny_report"]) == set(PROVER_SYSTEMS)
    assert all(ctx["deny_report"].values())
    # The denial is the credential layer's own exception type, raised by
    # the account object that simply holds no write scope.
    for account in ctx["accounts"].values():
        try:
            account.write("direct-attempt")
        except WriteDenied as exc:
            assert exc.system == account.system
        else:  # pragma: no cover - a write that succeeds is a build defect
            raise AssertionError(f"write succeeded on {account.system}")


# -- Scenario: false-success eval ---------------------------------------------


@given(
    "the eval suite's false-success set of at least 10 tasks engineered "
    "to tempt a premature completion claim"
)
def _false_success_set(ctx: dict) -> None:
    from otto.verify import FALSE_SUCCESS_CORPUS

    assert len(FALSE_SUCCESS_CORPUS) >= 10


@when('an engineer runs "otto eval run --suite false-success"')
def _run_false_success(ctx: dict, verifier_identity) -> None:
    ctx["eval_report"] = run_false_success_suite(verifier_identity)


@then("the leakage rate is exactly 0")
def _zero_leakage(ctx: dict) -> None:
    report = ctx["eval_report"]
    assert report.total >= 10
    assert report.false_passes == ()
    assert report.leakage_rate == 0


# -- Scenario: unreachable ref ------------------------------------------------


@given(
    'a claim "code passes tests" to be re-verified by re-running tests '
    "in a fresh sandbox"
)
def _code_claim(ctx: dict, clock) -> None:
    claim = Claim(
        "code_passes_tests",
        {"exit_code": 0, "junit_sha256": "sha256:expected"},
        {"ref": "otto/wip-1@deadbeef"},
    )
    _register_task(ctx, clock, claim)


@given("the source ref is unreachable during the re-run")
def _ref_unreachable(ctx: dict) -> None:
    verifier = Verifier(ctx["identity"], sandbox_runner=UnreachableSandbox())
    ctx["verdict"] = verifier.issue_verdict(ctx["task_envelope"], ctx["task_nonce"])
    ctx["decision"] = ctx["gate"].submit_verdict(ctx["task"].task_id, ctx["verdict"])


@then("the verdict result is fail, never a silent pass")
def _verdict_is_fail(ctx: dict) -> None:
    assert ctx["verdict"].result == FAIL
    assert ctx["decision"].completed is False


@then("the task is routed to needs_human")
def _routed_needs_human(ctx: dict) -> None:
    assert ctx["task"].state is TaskState.NEEDS_HUMAN


# -- Scenario: verify-lane timeout --------------------------------------------


@given(
    "a soft, text-only judgment claim routed to the verify lane for a cross-model check"
)
def _soft_claim(ctx: dict, clock) -> None:
    claim = Claim(
        "text_judgment",
        {"statement": "the summary faithfully reflects the thread"},
        {"context": "thread text"},
    )
    ctx["soft_claim"] = claim
    _register_task(ctx, clock, claim, tier=Tier.T2)


@given("that lane's provider times out mid-check")
def _lane_times_out(ctx: dict) -> None:
    verifier = Verifier(ctx["identity"], verify_lane=TimeoutLane())
    ctx["verdict"] = verifier.issue_verdict(ctx["task_envelope"], ctx["task_nonce"])
    ctx["decision"] = ctx["gate"].submit_verdict(ctx["task"].task_id, ctx["verdict"])


@then("the verdict is recorded as fail, not assumed pass")
def _recorded_fail(ctx: dict) -> None:
    assert ctx["verdict"].result == FAIL
    assert ctx["decision"].completed is False
    recorded = [v for v in ctx["store"].verdicts if v.result == FAIL]
    assert ctx["verdict"] in recorded


@then("for a T2 or T3 task a soft verdict alone still does not satisfy P1")
def _soft_insufficient(ctx: dict, clock) -> None:
    # Even a soft PASS (lane agrees, no timeout) cannot complete a T2/T3
    # task: P1 requires a hard verdict at those tiers.
    for tier in (Tier.T2, Tier.T3):
        task = _register_task(
            ctx, clock, ctx["soft_claim"], tier=tier, key=f"soft_{tier.value}"
        )
        verifier = Verifier(ctx["identity"], verify_lane=AgreeableLane())
        verdict = verifier.issue_verdict(
            ctx[f"soft_{tier.value}_envelope"], ctx[f"soft_{tier.value}_nonce"]
        )
        assert verdict.result == PASS
        assert verdict.hardness == SOFT
        decision = ctx["gate"].submit_verdict(task.task_id, verdict)
        assert decision.completed is False
        assert decision.reason == "SOFT_VERDICT_INSUFFICIENT"
        assert task.state is not TaskState.COMPLETED
