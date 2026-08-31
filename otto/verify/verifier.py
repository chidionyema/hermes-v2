"""Verifier core: checks a claimed-work envelope, signs a verdict.

Every claim is verified by the cheapest *deterministic* method for its
type (spec section 7 table), through the prover's own dependencies —
never through the orchestrator's tool output. Every environment failure
(unreachable ref, unreachable artifact, provider timeout, unknown claim
type, missing checker dependency) converts to a *fail* outcome. There is
no code path from an exception to a pass: the verifier fails closed.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from typing import Any, Protocol

from otto.verify.errors import (
    ArtifactUnreachable,
    ProviderTimeout,
    SelfCertificationError,
    SourceUnreachable,
    StateUnreachable,
)
from otto.verify.identity import VerifierIdentity
from otto.verify.model import (
    FAIL,
    HARD,
    PASS,
    SOFT,
    Claim,
    ClaimEnvelope,
    Verdict,
    new_trace_id,
)

CODE_PASSES_TESTS = "code_passes_tests"
ARTIFACT_PRODUCED = "artifact_produced"
SYSTEM_STATE = "system_state"
SOURCE_SAYS = "source_says"
TEXT_JUDGMENT = "text_judgment"


@dataclass(frozen=True)
class RerunResult:
    """Outcome of a fresh-sandbox test re-run from the pushed ref."""

    exit_code: int
    junit_sha256: str


class SandboxRunner(Protocol):
    """Re-runs tests in a fresh sandbox; raises SourceUnreachable."""

    def rerun(self, ref: str) -> RerunResult: ...


class ArtifactFetcher(Protocol):
    """Independently fetches artifact bytes; raises ArtifactUnreachable."""

    def fetch(self, locator: str) -> bytes: ...


class StateReader(Protocol):
    """Reads live state with the prover's own read-only credentials."""

    def read(self, target: str) -> Any: ...


class SourceFetcher(Protocol):
    """Fetches a source document; raises SourceUnreachable."""

    def fetch(self, url: str) -> str: ...


class VerifyLaneClient(Protocol):
    """Cross-model check on the verify lane; raises ProviderTimeout."""

    def supports(self, statement: str, context: str) -> bool: ...


@dataclass(frozen=True)
class CheckOutcome:
    passed: bool
    method: str
    hardness: str
    detail: str


_FAILURES = (
    SourceUnreachable,
    ArtifactUnreachable,
    StateUnreachable,
    ProviderTimeout,
)


class Verifier:
    """Issues signed verdicts over claimed-work envelopes.

    All checker dependencies are the *prover's own* (its sandbox, its
    read-only credentials, its verify-lane client). A missing dependency
    means the claim type cannot be verified and the outcome is fail.
    """

    def __init__(
        self,
        identity: VerifierIdentity,
        *,
        sandbox_runner: SandboxRunner | None = None,
        artifact_fetcher: ArtifactFetcher | None = None,
        state_reader: StateReader | None = None,
        source_fetcher: SourceFetcher | None = None,
        verify_lane: VerifyLaneClient | None = None,
    ) -> None:
        self._identity = identity
        self._sandbox_runner = sandbox_runner
        self._artifact_fetcher = artifact_fetcher
        self._state_reader = state_reader
        self._source_fetcher = source_fetcher
        self._verify_lane = verify_lane

    @property
    def identity(self) -> VerifierIdentity:
        return self._identity

    def issue_verdict(self, envelope: ClaimEnvelope, nonce: str) -> Verdict:
        """Check every claim, then sign one verdict for the envelope.

        Refuses outright (P1 build defect) when the envelope's builder
        identity is this verifier's own identity.
        """
        if envelope.builder_identity == self._identity.name:
            raise SelfCertificationError(self._identity.name)

        outcomes = [self._check(claim) for claim in envelope.claims]
        all_passed = bool(outcomes) and all(o.passed for o in outcomes)
        hardness = SOFT if any(o.hardness == SOFT for o in outcomes) else HARD
        verdict = Verdict(
            verdict_id=new_trace_id(),
            task_id=envelope.task_id,
            nonce=nonce,
            claim_hash=envelope.claim_hash(),
            method="+".join(dict.fromkeys(o.method for o in outcomes)) or "none",
            evidence={
                "outcomes": [
                    {
                        "passed": o.passed,
                        "method": o.method,
                        "hardness": o.hardness,
                        "detail": o.detail,
                    }
                    for o in outcomes
                ]
            },
            result=PASS if all_passed else FAIL,
            hardness=hardness,
            prover_key_id=self._identity.key_id,
            sig="",
        )
        signature = self._identity.sign(verdict.signing_payload())
        return Verdict(
            **{
                **verdict.as_payload(),
                "sig": base64.b64encode(signature).decode(),
            }
        )

    # -- deterministic checks, one per claim type ---------------------------

    def _check(self, claim: Claim) -> CheckOutcome:
        checker = {
            CODE_PASSES_TESTS: self._check_rerun_tests,
            ARTIFACT_PRODUCED: self._check_artifact,
            SYSTEM_STATE: self._check_state,
            SOURCE_SAYS: self._check_source,
            TEXT_JUDGMENT: self._check_text_judgment,
        }.get(claim.claim_type)
        if checker is None:
            return CheckOutcome(
                passed=False,
                method="none",
                hardness=HARD,
                detail=f"no deterministic method for {claim.claim_type!r}; fail closed",
            )
        try:
            return checker(claim)
        except _FAILURES as exc:
            return CheckOutcome(
                passed=False,
                method=checker.__name__.removeprefix("_check_"),
                hardness=HARD,
                detail=f"{exc.__class__.__name__}: cannot verify; fail closed",
            )

    def _check_rerun_tests(self, claim: Claim) -> CheckOutcome:
        if self._sandbox_runner is None:
            return _no_dependency("rerun_tests")
        ref = str(claim.evidence_spec.get("ref", ""))
        result = self._sandbox_runner.rerun(ref)
        ok = (
            result.exit_code == 0
            and result.exit_code == claim.claimed.get("exit_code")
            and result.junit_sha256 == claim.claimed.get("junit_sha256")
        )
        return CheckOutcome(
            passed=ok,
            method="rerun_tests",
            hardness=HARD,
            detail=f"fresh rerun of {ref!r} exit={result.exit_code}",
        )

    def _check_artifact(self, claim: Claim) -> CheckOutcome:
        if self._artifact_fetcher is None:
            return _no_dependency("artifact_hash")
        locator = str(claim.evidence_spec.get("locator", ""))
        actual = hashlib.sha256(self._artifact_fetcher.fetch(locator)).hexdigest()
        ok = f"sha256:{actual}" == claim.claimed.get("sha256")
        return CheckOutcome(
            passed=ok,
            method="artifact_hash",
            hardness=HARD,
            detail=f"independent fetch of {locator!r}",
        )

    def _check_state(self, claim: Claim) -> CheckOutcome:
        if self._state_reader is None:
            return _no_dependency("state_read")
        target = str(claim.evidence_spec.get("target", ""))
        actual = self._state_reader.read(target)
        ok = actual == claim.claimed.get("value")
        return CheckOutcome(
            passed=ok,
            method="state_read",
            hardness=HARD,
            detail=f"prover read of {target!r} with its own credentials",
        )

    def _check_source(self, claim: Claim) -> CheckOutcome:
        if self._source_fetcher is None:
            return _no_dependency("source_fetch")
        url = str(claim.evidence_spec.get("url", ""))
        text = self._source_fetcher.fetch(url)
        ok = str(claim.claimed.get("text", "")) in text
        return CheckOutcome(
            passed=ok,
            method="source_fetch",
            hardness=HARD,
            detail=f"containment check against {url!r}",
        )

    def _check_text_judgment(self, claim: Claim) -> CheckOutcome:
        if self._verify_lane is None:
            return _no_dependency("cross_model")
        supported = self._verify_lane.supports(
            str(claim.claimed.get("statement", "")),
            str(claim.evidence_spec.get("context", "")),
        )
        return CheckOutcome(
            passed=supported,
            method="cross_model",
            hardness=SOFT,
            detail="cross-model check on the verify lane",
        )


def _no_dependency(method: str) -> CheckOutcome:
    return CheckOutcome(
        passed=False,
        method=method,
        hardness=HARD,
        detail=f"prover has no {method} dependency wired; fail closed",
    )
