"""Otto CP3 — Verification Plane core (crew#768, spec section 7).

No task reaches ``completed`` without an Ed25519-signed verdict from
this plane referencing that task's own ULID, its single-use nonce, and
the hash of its registered claim package (constitution P1). The
completion gate holds public keys only; signing key material loads from
a path named by an environment variable (``OTTO_VERIFIER_KEY_PATH``) —
credential separation by construction. Every failure mode — forged or
replayed verdicts, tampered claims, unreachable refs, provider
timeouts, an unreachable verdict store — fails closed.

R64 compliance: this component contains no prompts, so DSPy does not
apply here; any future prompt work (the cross-model verify lane's
judge) belongs to the router component, not this package.
"""

from __future__ import annotations

from otto.verify.bus import RecordingBus, VerdictBus, verdict_subject
from otto.verify.credentials import (
    PROVER_SYSTEMS,
    ReadOnlyServiceAccount,
    WriteDenied,
    mint_prover_accounts,
    prover_write_deny_report,
)
from otto.verify.errors import (
    ArtifactUnreachable,
    KeyMaterialMissing,
    ProviderTimeout,
    SelfCertificationError,
    SourceUnreachable,
    StateUnreachable,
    StoreUnreachable,
)
from otto.verify.eval_hook import (
    FALSE_SUCCESS_CORPUS,
    FALSE_SUCCESS_SUITE,
    CorpusItem,
    EvalReport,
    run_false_success_suite,
)
from otto.verify.identity import (
    DEFAULT_KEY_PATH_ENV,
    VerifierIdentity,
    load_identity,
)
from otto.verify.ledger import (
    CompletionDecision,
    CompletionGate,
    Task,
    TaskState,
    Tier,
)
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
from otto.verify.store import (
    InMemoryVerdictStore,
    UnreachableVerdictStore,
    VerdictStore,
)
from otto.verify.verifier import RerunResult, Verifier, VerifierConfig

__all__ = [
    "FAIL",
    "FALSE_SUCCESS_CORPUS",
    "FALSE_SUCCESS_SUITE",
    "HARD",
    "PASS",
    "PROVER_SYSTEMS",
    "SOFT",
    "ArtifactUnreachable",
    "Claim",
    "ClaimEnvelope",
    "CompletionDecision",
    "CompletionGate",
    "CorpusItem",
    "DEFAULT_KEY_PATH_ENV",
    "EvalReport",
    "InMemoryVerdictStore",
    "KeyMaterialMissing",
    "ProviderTimeout",
    "ReadOnlyServiceAccount",
    "RecordingBus",
    "RerunResult",
    "SelfCertificationError",
    "SourceUnreachable",
    "StateUnreachable",
    "StoreUnreachable",
    "Task",
    "TaskState",
    "Tier",
    "UnreachableVerdictStore",
    "Verdict",
    "VerdictBus",
    "VerdictStore",
    "Verifier",
    "VerifierConfig",
    "VerifierIdentity",
    "WriteDenied",
    "load_identity",
    "mint_prover_accounts",
    "new_trace_id",
    "prover_write_deny_report",
    "run_false_success_suite",
    "verdict_subject",
]
