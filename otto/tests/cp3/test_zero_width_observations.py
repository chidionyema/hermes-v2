"""Regression: zero-width code points cannot rig a verification check.

Independent verifier probe (crew#768 hardening wave): U+200B/U+200C/
U+200D/U+FEFF are invisible but are not whitespace, so they survived
``_normalise``. Two visually identical strings graded differently, and a
claimed text padded with invisible characters counted toward the
minimum-match length while displaying almost nothing — the builder
controls both the claimed text and the source URL, so invisible padding
defeated the only guard against trivially-true containment claims.

The rule now: claimed text is evidence and is REFUSED when it carries
zero-width code points (fail closed, loud detail); fetched observations
are stripped of them, with the stripping recorded in the outcome detail.
"""

from __future__ import annotations

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from otto.verify import FAIL, PASS, Claim, ClaimEnvelope, Verifier
from otto.verify.identity import VerifierIdentity

ZERO_WIDTH_SPACE = "\u200b"


class _Source:
    def __init__(self, document: str) -> None:
        self.document = document

    def fetch(self, url: str) -> str:
        return self.document


def _verifier(document: str) -> Verifier:
    identity = VerifierIdentity(
        name="prover-lane",
        key_id="test-key",
        private_key=Ed25519PrivateKey.generate(),
    )
    return Verifier(identity, source_fetcher=_Source(document))


def _verdict(verifier: Verifier, claimed_text: str):
    envelope = ClaimEnvelope(
        task_id="task-zero-width",
        builder_identity="builder-lane",
        claims=(
            Claim(
                "source_says",
                {"text": claimed_text},
                {"url": "https://example.invalid/doc"},
            ),
        ),
    )
    return verifier.issue_verdict(envelope, nonce="nonce-1")


def test_invisible_padding_cannot_reach_the_length_minimum() -> None:
    # Visually the claim is just "a"; the padding used to lift it past the
    # 8-character minimum, and the builder-chosen document contained the
    # same padded string, so the containment check passed on nothing.
    padded = "a" + ZERO_WIDTH_SPACE * 10
    verifier = _verifier(document=f"salted document holding {padded} exactly")
    verdict = _verdict(verifier, padded)
    assert verdict.result == FAIL
    detail = verdict.evidence["outcomes"][0]["detail"]
    assert "zero-width" in detail


def test_visually_identical_source_still_matches() -> None:
    # The observed document carries an invisible joiner inside the word;
    # a clean claim about the visually identical text must still ground.
    document = f"status: deploy{ZERO_WIDTH_SPACE}ment finished at noon"
    verifier = _verifier(document)
    verdict = _verdict(verifier, "deployment finished")
    assert verdict.result == PASS
    detail = verdict.evidence["outcomes"][0]["detail"]
    assert "stripped" in detail


def test_clean_claim_against_clean_source_is_unchanged() -> None:
    verifier = _verifier("status: deployment finished at noon")
    verdict = _verdict(verifier, "deployment finished")
    assert verdict.result == PASS


def test_zero_width_statement_is_refused_on_the_verify_lane() -> None:
    class _AlwaysSupports:
        def supports(self, statement: str, context: str) -> bool:
            return True

    identity = VerifierIdentity(
        name="prover-lane",
        key_id="test-key",
        private_key=Ed25519PrivateKey.generate(),
    )
    verifier = Verifier(identity, verify_lane=_AlwaysSupports())
    envelope = ClaimEnvelope(
        task_id="task-zero-width-judgment",
        builder_identity="builder-lane",
        claims=(
            Claim(
                "text_judgment",
                {"statement": f"all{ZERO_WIDTH_SPACE} tests green"},
                {"context": "run log"},
            ),
        ),
    )
    verdict = verifier.issue_verdict(envelope, nonce="nonce-2")
    assert verdict.result == FAIL
    assert "zero-width" in verdict.evidence["outcomes"][0]["detail"]
