"""Shared fixtures for the CP3 Verification Plane BDD suite.

``ctx`` is the one piece of mutable state Given/When/Then steps pass
between each other within a scenario (same idiom as the CP2 suite).

Keys are ephemeral, generated in-fixture, and written only under
``tmp_path``; the identity is loaded through the real env-named-path
route (``OTTO_VERIFIER_KEY_PATH``) so the loading code under test is the
production code. No key material or secret value is ever a literal.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from otto.verify import DEFAULT_KEY_PATH_ENV, VerifierIdentity, load_identity


def pytest_configure(config: pytest.Config) -> None:
    # Gherkin tags on the feature become pytest marks (pytest-bdd); register
    # them locally so this suite's own run has no unregistered-mark warning.
    config.addinivalue_line("markers", "cp3: CP3 Verification Plane scenarios")


class FakeClock:
    """Deterministic clock so deadline scenarios need no sleeping."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def write_ephemeral_key(path: Path) -> None:
    """Generate a fresh Ed25519 key and write its raw bytes to ``path``."""
    key = Ed25519PrivateKey.generate()
    path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )


@pytest.fixture
def ctx() -> dict:
    return {}


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def verifier_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> VerifierIdentity:
    """A verification-plane identity backed by an ephemeral in-fixture key."""
    key_path = tmp_path / "vp-signing.key"
    write_ephemeral_key(key_path)
    monkeypatch.setenv(DEFAULT_KEY_PATH_ENV, str(key_path))
    return load_identity(name="verification-plane", key_id="vp-ed25519-test")


@pytest.fixture
def rogue_identity(tmp_path: Path) -> VerifierIdentity:
    """A different, untrusted key claiming the same key id (the forger)."""
    key = Ed25519PrivateKey.generate()
    return VerifierIdentity(name="rogue-lane", key_id="vp-ed25519-test", signer=key)
