"""The transactional outbox, Python translation of decision D3 of ADR-0012
(`idp/platform/messaging/outbox/outbox.go`): a task's submission is written
to a Postgres row in the same transaction as whatever else the caller is
doing, and a separate relay is the only writer to the bus. A task is never
lost between "committed to the database" and "published to JetStream" —
if the process dies in between, the row is still there with
`published_at IS NULL`, and the relay picks it up on the next pass. This
is what makes the spec's NATS-partition scenario (§17) a recoverable
event instead of a lost task.

Schema, relay algorithm (`SELECT ... FOR UPDATE SKIP LOCKED`, mark inside
the same transaction the publish succeeded in) and the crash-injection
hook are the same shape as the Go original — same reasoning, same
concurrency-safety argument, ported to asyncpg because this build is
Python (hermes-agent's own harness) and Go is not in scope here (LAW 43:
the *pattern* is reused, not a language runtime the rest of this repo
does not carry).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Awaitable, Callable

import asyncpg

from otto.spine import subjects
from otto.spine.bus import Bus

SCHEMA = """
CREATE TABLE IF NOT EXISTS otto_outbox (
  id           bigserial PRIMARY KEY,
  task_id      text NOT NULL,
  seq          bigint NOT NULL,
  subject      text NOT NULL,
  headers      jsonb NOT NULL,
  payload      bytea NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT now(),
  published_at timestamptz,
  UNIQUE (task_id, seq)
);
CREATE INDEX IF NOT EXISTS otto_outbox_unpublished
  ON otto_outbox (created_at) WHERE published_at IS NULL;
"""


def default_dsn() -> str:
    # LAW 46: no hardcoded host/account. The estate's Postgres pattern
    # (idp platform/hindsight/postgres.yaml) reads its DSN from a Secret
    # mounted as env; the fallback here is only for a bare local run.
    return os.environ.get("OTTO_POSTGRES_DSN", "postgresql://localhost:5432/otto")


@dataclass(frozen=True)
class RelayResult:
    task_id: str
    seq: int
    subject: str
    duplicate: bool
    stream_seq: int


class RelayCrashed(Exception):
    """Raised by `Relay.once()` when the caller's `crash_after_publish`
    hook fires: proves the at-least-once path the same way the Go relay's
    `CrashAfterPublish` does — the row is published on the bus but the
    Postgres row is deliberately left unmarked, exactly what a process
    death between those two steps would leave behind."""


class Outbox:
    """Owns the schema and the enqueue path. A caller obtains a connection
    (or transaction) from its own pool and calls `enqueue` inside it —
    this class never opens a transaction itself, so it composes with
    whatever transaction the caller (e.g. task submission) is already in."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def ensure_schema(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(SCHEMA)

    async def enqueue(
        self,
        conn: asyncpg.Connection,
        *,
        task_id: str,
        seq: int,
        subject: str,
        payload: bytes,
        headers: dict[str, str] | None = None,
    ) -> None:
        subjects.validate(subject)
        hdr = dict(headers or {})
        hdr["Nats-Msg-Id"] = subjects.dedupe_id(task_id, seq)
        await conn.execute(
            "INSERT INTO otto_outbox (task_id, seq, subject, headers, payload) "
            "VALUES ($1, $2, $3, $4::jsonb, $5) "
            "ON CONFLICT (task_id, seq) DO NOTHING",
            task_id,
            seq,
            subject,
            json.dumps(hdr),
            payload,
        )


class Relay:
    """The only process allowed to publish an outbox row to the bus. Safe
    to run more than one instance concurrently (`FOR UPDATE SKIP LOCKED`);
    safe to crash mid-publish (`Nats-Msg-Id` makes a re-publish a no-op on
    the broker side, per the `bus.py` smoke test above)."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        bus: Bus,
        *,
        crash_after_publish: Callable[[str, int], Awaitable[bool] | bool] | None = None,
    ) -> None:
        self._pool = pool
        self._bus = bus
        self._crash_after_publish = crash_after_publish

    async def once(self) -> list[RelayResult]:
        """Drain everything currently unpublished. Returns what it
        actually relayed; raises `RelayCrashed` (after appending the
        crashed row's result) if the crash hook fired on this pass, same
        semantics as the Go relay's `ErrCrashed`."""
        out: list[RelayResult] = []
        while True:
            row, more, crashed = await self._one()
            if row is not None:
                out.append(row)
            if crashed:
                raise RelayCrashed(f"{row.task_id}:{row.seq}" if row else "unknown")
            if not more:
                return out

    async def _one(self) -> tuple[RelayResult | None, bool, bool]:
        # `_CrashSignal` forces the transaction block to roll back (asyncpg
        # only commits when the `async with` body exits *without* raising)
        # while still letting this method hand the caller the RelayResult
        # for the row it published-but-did-not-mark — same observable
        # state a real process death between publish and UPDATE would
        # leave: the bus has the message, Postgres still says unpublished.
        class _CrashSignal(Exception):
            def __init__(self, result: RelayResult) -> None:
                self.result = result

        async with self._pool.acquire() as conn:
            try:
                async with conn.transaction():
                    record = await conn.fetchrow(
                        "SELECT id, task_id, seq, subject, headers, payload FROM otto_outbox "
                        "WHERE published_at IS NULL ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED"
                    )
                    if record is None:
                        return None, False, False

                    hdr = json.loads(record["headers"])
                    ack = await self._bus.js.publish(
                        record["subject"], bytes(record["payload"]), headers=hdr
                    )
                    result = RelayResult(
                        task_id=record["task_id"],
                        seq=record["seq"],
                        subject=record["subject"],
                        duplicate=bool(ack.duplicate),
                        stream_seq=ack.seq,
                    )

                    crash = self._crash_after_publish
                    if crash is not None:
                        fired = crash(record["task_id"], record["seq"])
                        if hasattr(fired, "__await__"):
                            fired = await fired
                        if fired:
                            raise _CrashSignal(result)

                    await conn.execute(
                        "UPDATE otto_outbox SET published_at = now() WHERE id = $1",
                        record["id"],
                    )
                    return result, True, False
            except _CrashSignal as sig:
                return sig.result, False, True

    async def unpublished(self) -> int:
        async with self._pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT count(*) FROM otto_outbox WHERE published_at IS NULL"
            )
