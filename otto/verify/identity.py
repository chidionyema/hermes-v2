"""Verifier identity: the only holder of verdict-signing key material.

Separate credentials by construction (spec section 7): the signing key is
loaded from a file whose *path* arrives via an environment variable name —
never a literal in code or config. The orchestrator process simply never
sets that variable and therefore physically cannot mint verdicts (P1).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from otto.verify.errors import KeyMaterialMissing

#: Name of the environment variable holding the *path* to the raw 32-byte
#: Ed25519 private key file. The value is a path, never key material.
DEFAULT_KEY_PATH_ENV = "OTTO_VERIFIER_KEY_PATH"

_ED25519_RAW_KEY_LEN = 32


@dataclass(frozen=True)
class VerifierIdentity:
    """The Verification Plane's signing identity.

    ``name`` is the lane identity (compared against the builder identity
    on every claim envelope — equality is a P1 build defect). ``key_id``
    is published alongside every verdict so the completion gate can pick
    the right trusted public key.
    """

    name: str
    key_id: str
    private_key: Ed25519PrivateKey = field(repr=False)

    def sign(self, payload: bytes) -> bytes:
        return self.private_key.sign(payload)

    def public_key_bytes(self) -> bytes:
        return self.private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )


def load_identity(
    name: str,
    key_id: str,
    env_var: str = DEFAULT_KEY_PATH_ENV,
) -> VerifierIdentity:
    """Load the signing identity from the file named by ``env_var``.

    Fail closed: a missing variable, a missing file, or malformed key
    material raises :class:`KeyMaterialMissing`. No fallback key exists.
    """
    key_path = os.environ.get(env_var)
    if not key_path:
        raise KeyMaterialMissing(
            f"environment variable {env_var} is unset; the verifier "
            "cannot sign and therefore no task can complete"
        )
    path = Path(key_path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise KeyMaterialMissing(
            f"key file named by {env_var} is unreadable: {exc.__class__.__name__}"
        ) from exc
    if len(raw) != _ED25519_RAW_KEY_LEN:
        raise KeyMaterialMissing(
            f"key file named by {env_var} is not a raw 32-byte Ed25519 key"
        )
    return VerifierIdentity(
        name=name,
        key_id=key_id,
        private_key=Ed25519PrivateKey.from_private_bytes(raw),
    )
