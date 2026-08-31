"""Data model: claims, claim envelopes, and Ed25519-signed verdicts.

The verdict record follows spec section 7 with two hardening additions:
a per-task ``nonce`` (minted by the completion gate when the task reaches
``awaiting_verdict``, single-use, so a captured verdict cannot be
replayed) and an explicit binding of the signature over every field of
the verdict via a canonical JSON encoding.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping

from ulid import ULID

PASS = "pass"  # noqa: S105 — a verdict result value, not a credential
FAIL = "fail"

HARD = "hard"
SOFT = "soft"


def new_trace_id() -> str:
    """Mint a ULID trace id (doubles as the OTel trace id, spec section 3)."""
    return str(ULID())


def canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    """Deterministic encoding used for both hashing and signing."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


@dataclass(frozen=True)
class Claim:
    """One claim from the claimed-work envelope.

    ``claimed`` is what the builder asserts; ``evidence_spec`` is how the
    prover is to check it independently (a ref to re-run, a locator to
    re-fetch, a target to re-read). The prover never trusts ``claimed``.
    """

    claim_type: str
    claimed: Mapping[str, Any]
    evidence_spec: Mapping[str, Any] = field(default_factory=dict)

    def as_payload(self) -> dict[str, Any]:
        return {
            "claim_type": self.claim_type,
            "claimed": dict(self.claimed),
            "evidence_spec": dict(self.evidence_spec),
        }


@dataclass(frozen=True)
class ClaimEnvelope:
    """The claimed-work package published when a task hits awaiting_verdict."""

    task_id: str
    builder_identity: str
    claims: tuple[Claim, ...]

    def claim_hash(self) -> str:
        digest = hashlib.sha256(
            canonical_bytes({"claims": [c.as_payload() for c in self.claims]})
        ).hexdigest()
        return f"sha256:{digest}"


@dataclass(frozen=True)
class Verdict:
    """An Ed25519-signed verdict (spec section 7 verdict record)."""

    verdict_id: str
    task_id: str
    nonce: str
    claim_hash: str
    method: str
    evidence: Mapping[str, Any]
    result: str  # PASS | FAIL
    hardness: str  # HARD | SOFT
    prover_key_id: str
    sig: str  # base64, over signing_payload()

    def signing_payload(self) -> bytes:
        """Every field except the signature, canonically encoded."""
        return canonical_bytes(
            {
                "verdict_id": self.verdict_id,
                "task_id": self.task_id,
                "nonce": self.nonce,
                "claim_hash": self.claim_hash,
                "method": self.method,
                "evidence": dict(self.evidence),
                "result": self.result,
                "hardness": self.hardness,
                "prover_key_id": self.prover_key_id,
            }
        )

    def sig_bytes(self) -> bytes:
        return base64.b64decode(self.sig)

    def as_payload(self) -> dict[str, Any]:
        return {
            "verdict_id": self.verdict_id,
            "task_id": self.task_id,
            "nonce": self.nonce,
            "claim_hash": self.claim_hash,
            "method": self.method,
            "evidence": dict(self.evidence),
            "result": self.result,
            "hardness": self.hardness,
            "prover_key_id": self.prover_key_id,
            "sig": self.sig,
        }
