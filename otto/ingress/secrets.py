"""Turning a secret reference into a secret value, at request time.

The ``channel_binding`` table stores references, never material. This
module is the one place a reference becomes a value, so there is exactly
one line to audit when asking "where could a customer's channel secret
leak?" — and it never logs, never returns the value in an error, and
never puts it in an exception message.

``EnvSecretResolver`` is the implementation that runs today: it reads a
value the platform's secret store has already projected into the
process's environment, which is how every other Otto lane receives
secrets (LAW 46 — the reference is config, the value is never a literal).
A Vault-backed resolver implements the same Protocol and changes nothing
above it.
"""

from __future__ import annotations

import os
import re
from typing import Protocol


class SecretNotFound(LookupError):
    """A binding names a secret the resolver cannot produce.

    Carries the reference, which is not secret, and never the value.
    """

    def __init__(self, secret_ref: str) -> None:
        self.secret_ref = secret_ref
        super().__init__(f"no secret is available for reference {secret_ref!r}")


class SecretResolver(Protocol):
    def resolve(self, secret_ref: str) -> str:
        """The secret value for this reference, or ``SecretNotFound``."""
        ...


_UNSAFE = re.compile(r"[^A-Za-z0-9]+")


def env_var_name(secret_ref: str, prefix: str = "OTTO_CHANNEL_SECRET_") -> str:
    """The environment variable a reference maps to.

    ``vault://otto/acme/telegram`` becomes
    ``OTTO_CHANNEL_SECRET_VAULT_OTTO_ACME_TELEGRAM``. The mapping is
    mechanical so an operator can read a binding row and know exactly
    which projected key it needs, without a second lookup table to keep
    in sync.
    """
    return prefix + _UNSAFE.sub("_", secret_ref).strip("_").upper()


class EnvSecretResolver:
    """Resolve from the process environment, where the platform's secret
    store projects channel secrets."""

    def __init__(
        self,
        environ: dict[str, str] | None = None,
        prefix: str = "OTTO_CHANNEL_SECRET_",
    ) -> None:
        self._environ = environ
        self._prefix = prefix

    def resolve(self, secret_ref: str) -> str:
        env = os.environ if self._environ is None else self._environ
        value = env.get(env_var_name(secret_ref, self._prefix), "")
        if not value:
            raise SecretNotFound(secret_ref)
        return value
