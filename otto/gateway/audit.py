"""Audit event and pluggable emitter — "OTTO NEEDS TOTAL COVERAGE" (founder).

The gateway calls the emitter exactly once per tool call, whether the call
executed or was refused, so an emitter that logs everything gives total
coverage by construction rather than by convention.

The emitter is a ``Protocol``, not a concrete transport: this checkpoint is
the in-process core (per the task brief, "transport later"). Wiring
``AuditEmitter`` to ``otto.tool.v1.req``/``otto.tool.v1.res`` on JetStream
(spec section 4) is a follow-up that satisfies this same interface, so no
caller of ``ToolGateway`` changes when that lands.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class AuditEvent:
    envelope_id: str
    tool_name: str
    requested_tier: str
    effective_tier: str
    decision: str  # "executed" | "denied"
    duration_ms: float
    reason: str | None = None  # DenialReason.value, only set when decision == "denied"


class AuditEmitter(Protocol):
    """Pluggable audit sink. Anything with this shape can be wired in —
    an in-memory list for tests, a JetStream publisher for production, a
    stdout writer for a bare local run."""

    def emit(self, event: AuditEvent) -> None: ...


@dataclass
class InMemoryAuditEmitter:
    """Reference emitter: keeps every event in order, for tests and for a
    bare local run with no bus configured. Not a substitute for the
    JetStream-backed emitter the transport checkpoint adds — this emitter
    holds nothing across process restarts, which is exactly why the spec
    (P4, "if it isn't on the stream, it didn't happen") requires the real
    one before this reaches a network."""

    events: list[AuditEvent] = field(default_factory=list)

    def emit(self, event: AuditEvent) -> None:
        self.events.append(event)
