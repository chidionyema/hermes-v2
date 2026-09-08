"""Verdict bus: the one thing the prover and the orchestrator share.

Subjects follow spec section 4: ``otto.verdict.v1.pass`` and
``otto.verdict.v1.fail``. The integration wave backs this with the NATS
JetStream backbone; this package only fixes the interface and subjects.
"""

from __future__ import annotations

from typing import Any, Mapping, Protocol

from otto.verify.model import Verdict

VERDICT_SUBJECT_PREFIX = "otto.verdict.v1"


def verdict_subject(verdict: Verdict) -> str:
    return f"{VERDICT_SUBJECT_PREFIX}.{verdict.result}"


class VerdictBus(Protocol):
    """Publish-side of the bus, as seen from the Verification Plane."""

    def publish(self, subject: str, payload: Mapping[str, Any]) -> None: ...


class RecordingBus:
    """In-memory bus for tests and the local compose mode."""

    def __init__(self) -> None:
        self.published: list[tuple[str, dict[str, Any]]] = []

    def publish(self, subject: str, payload: Mapping[str, Any]) -> None:
        self.published.append((subject, dict(payload)))
