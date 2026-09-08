"""Falsification set beyond the BDD contract (crew#768 CP3).

Every test here is an attack on the completion gate that must be
refused: a tampered claim hash, a verdict replayed after its nonce is
consumed, self-certification by the building lane, an unreachable
verdict store, and missing signing-key material. Fail closed, every
path.
"""

from __future__ import annotations

import dataclasses
import hashlib

import pytest

from otto.verify import (
    DEFAULT_KEY_PATH_ENV,
    FAIL,
    Claim,
    ClaimEnvelope,
    CompletionGate,
    InMemoryVerdictStore,
    KeyMaterialMissing,
    RecordingBus,
    SelfCertificationError,
    Task,
    TaskState,
    Tier,
    UnreachableVerdictStore,
    Verifier,
    load_identity,
    new_trace_id,
)

_ARTIFACT = b"the artifact the builder actually produced"
_SHA = f"sha256:{hashlib.sha256(_ARTIFACT).hexdigest()}"


class _Artifacts:
    def fetch(self, locator: str) -> bytes:
        return _ARTIFACT


def _claim() -> Claim:
    return Claim("artifact_produced", {"sha256": _SHA}, {"locator": "oci://a/b"})


def _gate(verifier_identity, clock, store=None) -> CompletionGate:
    return CompletionGate(
        trusted_keys={verifier_identity.key_id: verifier_identity.public_key_bytes()},
        store=store if store is not None else InMemoryVerdictStore(),
        bus=RecordingBus(),
        clock=clock,
    )


def _awaiting_task(gate: CompletionGate, clock) -> tuple[Task, ClaimEnvelope, str]:
    task = Task(
        task_id=new_trace_id(),
        authority_ceiling=Tier.T1,
        deadline_s=600.0,
        created_at=clock(),
    )
    envelope = ClaimEnvelope(
        task_id=task.task_id, builder_identity="orchestrator", claims=(_claim(),)
    )
    nonce = gate.await_verdict(task, envelope)
    return task, envelope, nonce


def test_tampered_claim_hash_is_refused(verifier_identity, clock) -> None:
    gate = _gate(verifier_identity, clock)
    task, envelope, nonce = _awaiting_task(gate, clock)
    verifier = Verifier(verifier_identity, artifact_fetcher=_Artifacts())
    verdict = verifier.issue_verdict(envelope, nonce)

    # Swap the claim hash for a different (attacker-chosen) claim set and
    # re-sign nothing: the signature check itself refuses first.
    tampered = dataclasses.replace(
        verdict, claim_hash=f"sha256:{hashlib.sha256(b'other work').hexdigest()}"
    )
    decision = gate.submit_verdict(task.task_id, tampered)
    assert decision.completed is False
    assert decision.reason == "FORGED_BAD_SIGNATURE"
    assert task.state is TaskState.AWAITING_VERDICT

    # A tampered hash *re-signed by the trusted key* (an insider bug, not
    # an outsider) is still refused by the hash-binding check.
    resigned = verifier.issue_verdict(
        ClaimEnvelope(
            task_id=task.task_id,
            builder_identity="orchestrator",
            claims=(Claim("artifact_produced", {"sha256": _SHA}, {"locator": "x"}),),
        ),
        nonce,
    )
    assert resigned.claim_hash != envelope.claim_hash()
    decision = gate.submit_verdict(task.task_id, resigned)
    assert decision.completed is False
    assert decision.reason == "TAMPERED_CLAIM_HASH"
    assert task.state is TaskState.AWAITING_VERDICT


def test_replayed_nonce_is_refused_after_completion(verifier_identity, clock) -> None:
    gate = _gate(verifier_identity, clock)
    task, envelope, nonce = _awaiting_task(gate, clock)
    verifier = Verifier(verifier_identity, artifact_fetcher=_Artifacts())
    verdict = verifier.issue_verdict(envelope, nonce)

    first = gate.submit_verdict(task.task_id, verdict)
    assert first.completed is True

    # Replaying the same (valid, signed) verdict cannot complete again:
    # the task left awaiting_verdict and the nonce is consumed.
    replay = gate.submit_verdict(task.task_id, verdict)
    assert replay.completed is False
    assert replay.reason == "NOT_AWAITING_VERDICT"


def test_wrong_nonce_is_refused(verifier_identity, clock) -> None:
    gate = _gate(verifier_identity, clock)
    task, envelope, _nonce = _awaiting_task(gate, clock)
    verifier = Verifier(verifier_identity, artifact_fetcher=_Artifacts())
    stale = verifier.issue_verdict(envelope, "a-nonce-from-another-round")

    decision = gate.submit_verdict(task.task_id, stale)
    assert decision.completed is False
    assert decision.reason == "REPLAYED_NONCE"
    assert task.state is TaskState.AWAITING_VERDICT


def test_builder_lane_cannot_verify_its_own_work(verifier_identity) -> None:
    envelope = ClaimEnvelope(
        task_id=new_trace_id(),
        builder_identity=verifier_identity.name,
        claims=(_claim(),),
    )
    verifier = Verifier(verifier_identity, artifact_fetcher=_Artifacts())
    with pytest.raises(SelfCertificationError):
        verifier.issue_verdict(envelope, "n")


def test_unreachable_verdict_store_leaves_task_uncompletable(
    verifier_identity, clock
) -> None:
    gate = _gate(verifier_identity, clock, store=UnreachableVerdictStore())
    task, envelope, nonce = _awaiting_task(gate, clock)
    verifier = Verifier(verifier_identity, artifact_fetcher=_Artifacts())
    verdict = verifier.issue_verdict(envelope, nonce)

    decision = gate.submit_verdict(task.task_id, verdict)
    assert decision.completed is False
    assert decision.reason == "STORE_UNREACHABLE"
    # Loud, never silently completable: the task is routed to a human.
    assert task.state is TaskState.NEEDS_HUMAN


def test_missing_key_material_fails_closed(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv(DEFAULT_KEY_PATH_ENV, raising=False)
    with pytest.raises(KeyMaterialMissing):
        load_identity(name="verification-plane", key_id="vp-test")

    monkeypatch.setenv(DEFAULT_KEY_PATH_ENV, str(tmp_path / "absent.key"))
    with pytest.raises(KeyMaterialMissing):
        load_identity(name="verification-plane", key_id="vp-test")


def test_unknown_prover_key_is_refused(verifier_identity, rogue_identity, clock):
    gate = _gate(verifier_identity, clock)
    task, envelope, nonce = _awaiting_task(gate, clock)
    stranger = dataclasses.replace(rogue_identity, key_id="vp-unknown-key")
    verdict = Verifier(stranger, artifact_fetcher=_Artifacts()).issue_verdict(
        envelope, nonce
    )
    decision = gate.submit_verdict(task.task_id, verdict)
    assert decision.completed is False
    assert decision.reason == "FORGED_UNKNOWN_KEY"
    assert task.state is TaskState.AWAITING_VERDICT


def test_same_key_under_a_different_name_is_refused(verifier_identity) -> None:
    # The self-certification check is key material, not a label: a lane
    # presenting the verifier's own public key under a fresh name is the
    # same principal and is refused.
    envelope = ClaimEnvelope(
        task_id=new_trace_id(),
        builder_identity="a-lane-with-a-different-name",
        claims=(_claim(),),
        builder_public_key=verifier_identity.public_key_bytes(),
    )
    verifier = Verifier(verifier_identity, artifact_fetcher=_Artifacts())
    with pytest.raises(SelfCertificationError):
        verifier.issue_verdict(envelope, "n")


def test_malformed_signature_is_a_structured_refusal(verifier_identity, clock) -> None:
    gate = _gate(verifier_identity, clock)
    task, envelope, nonce = _awaiting_task(gate, clock)
    verifier = Verifier(verifier_identity, artifact_fetcher=_Artifacts())
    verdict = verifier.issue_verdict(envelope, nonce)

    garbage = dataclasses.replace(verdict, sig="!!!not-base64!!!")
    decision = gate.submit_verdict(task.task_id, garbage)
    assert decision.completed is False
    assert decision.reason == "MALFORMED_SIGNATURE"
    # The task lands where an absent verdict leaves it: still awaiting,
    # bound for needs_human at its deadline — never completed.
    assert task.state is TaskState.AWAITING_VERDICT


def test_trivial_source_claim_never_passes(verifier_identity) -> None:
    # The needle IS present in the source: a bare substring check would
    # emit a hard PASS. The class fix refuses it as unverifiable instead.
    class _Src:
        def fetch(self, url: str) -> str:
            return "a page that contains the letter a"

    for needle in ("", " \n\t  ", "a"):
        claim = Claim(
            "source_says", {"text": needle}, {"url": "https://example.invalid"}
        )
        envelope = ClaimEnvelope(
            task_id=new_trace_id(),
            builder_identity="orchestrator",
            claims=(claim,),
        )
        verdict = Verifier(verifier_identity, source_fetcher=_Src()).issue_verdict(
            envelope, "n"
        )
        assert verdict.result == FAIL, f"trivial needle {needle!r} passed"
