"""Exceptions for the Verification Plane.

The split follows the CP2 gateway convention: a *refusal to complete a
task* is never an exception — it is a structured ``CompletionDecision``
(see ``otto.verify.ledger``) the caller can render. Exceptions here are
either build defects (a lane holding both the builder and the verifier
identity) or environment failures the verifier converts into a *fail*
verdict or a fail-closed refusal — never into a silent pass.
"""

from __future__ import annotations


class SelfCertificationError(RuntimeError):
    """Raised when the lane that built the work asks to verify it (P1).

    This is a build defect, not a runtime refusal: by construction the
    orchestrator holds no verdict key material, so the only way this can
    fire is a deployment that wired one identity into both roles.
    """

    def __init__(self, identity: str) -> None:
        super().__init__(
            f"identity {identity!r} built the claimed work and may not "
            "verify it (constitution P1: no self-certification)"
        )
        self.identity = identity


class KeyMaterialMissing(RuntimeError):
    """Raised when the verifier signing key cannot be loaded.

    Fail closed: with no key there is no verdict, and with no verdict no
    task completes.
    """


class SourceUnreachable(RuntimeError):
    """A fresh-sandbox re-run could not reach the claimed source ref."""


class ArtifactUnreachable(RuntimeError):
    """An independently fetched artifact could not be retrieved."""


class StateUnreachable(RuntimeError):
    """The prover's own read of live system state failed."""


class ProviderTimeout(RuntimeError):
    """The cross-model verify lane's provider timed out mid-check."""


class StoreUnreachable(RuntimeError):
    """The verdict store cannot be reached; completion must fail closed."""
