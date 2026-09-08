"""Verdict store: durable record of every verdict, fail closed when down.

The interface is deliberately tiny so the integration wave can back it
with JetStream/Postgres. The one behavioral contract is P4 + fail
closed: if the store cannot record a verdict, the completion gate must
refuse completion — a task is never silently completable while the
record of *why* it completed cannot be written.
"""

from __future__ import annotations

from typing import Protocol

from otto.verify.errors import StoreUnreachable
from otto.verify.model import Verdict


class VerdictStore(Protocol):
    """Durable verdict storage."""

    def record(self, verdict: Verdict) -> None:
        """Persist the verdict, or raise :class:`StoreUnreachable`."""
        ...


class InMemoryVerdictStore:
    """Process-local store for tests and the local compose mode."""

    def __init__(self) -> None:
        self.verdicts: list[Verdict] = []

    def record(self, verdict: Verdict) -> None:
        self.verdicts.append(verdict)


class UnreachableVerdictStore:
    """Models the network-failure case: every record attempt fails."""

    def record(self, verdict: Verdict) -> None:
        raise StoreUnreachable("verdict store unreachable")
