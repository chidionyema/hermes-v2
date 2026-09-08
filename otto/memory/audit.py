"""Pluggable audit emission for the hygiene job.

The spec's spine lane owns the real event bus (NATS JetStream,
``otto.mem.v1.write``); this core defines the interface it will be
called through and ships a Postgres-backed default so a deletion is
never silently unaudited even before that wiring lands (spec P4:
"Everything on the bus. If it isn't on the stream, it didn't happen" -
this module's job is to make sure it is at least never nowhere).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

import psycopg


@dataclass(frozen=True)
class AuditEvent:
    fact_id: str
    action: str  # "expire" | "compact_duplicate" | "context_compact"
    reason: str
    detail: dict | None = None
    performed_at: datetime = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.performed_at is None:
            object.__setattr__(self, "performed_at", datetime.now(timezone.utc))


@runtime_checkable
class AuditEmitter(Protocol):
    def emit(self, event: AuditEvent) -> None: ...


class PostgresAuditEmitter:
    """Writes to ``otto_fact_audit`` outside the caller's own transaction.

    Not the default any more (otto/memory/hygiene.py and context.py write
    the audit row themselves, inside the same transaction as the change
    they are auditing - the outbox pattern: durable local record first,
    pluggable notification after commit). Kept for a caller that wants a
    second, independent Postgres write in addition to that guarantee."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def emit(self, event: AuditEvent) -> None:
        import json

        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO otto_fact_audit
                    (id, fact_id, action, reason, detail, performed_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    str(uuid.uuid4()),
                    event.fact_id,
                    event.action,
                    event.reason,
                    json.dumps(event.detail) if event.detail is not None else None,
                    event.performed_at,
                ),
            )
        self._conn.commit()


class NullAuditEmitter:
    """The default post-commit notifier: does nothing.

    Safe as the default precisely because it is not the durable record -
    the caller (hygiene.py, context.py) already wrote the audit row in
    the same transaction as the change before this is ever invoked. A
    caller that wants external notification (NATS, Telegram, ...) passes
    a real emitter instead; one that fails here can never cause an
    unaudited deletion, because the audit row already exists."""

    def emit(self, event: AuditEvent) -> None:
        return None


class InMemoryAuditEmitter:
    """Test double: collects events for assertion, no I/O."""

    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def emit(self, event: AuditEvent) -> None:
        self.events.append(event)


class FanoutAuditEmitter:
    """Composes multiple emitters (e.g. Postgres + a future NATS
    publisher) so a hygiene run is never limited to exactly one sink."""

    def __init__(self, *emitters: AuditEmitter) -> None:
        self._emitters = emitters

    def emit(self, event: AuditEvent) -> None:
        for emitter in self._emitters:
            emitter.emit(event)
