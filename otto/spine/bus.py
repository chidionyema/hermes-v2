"""The JetStream bus (spec §4, P4 of the constitution). Thin wrapper over
`nats-py`'s JetStream client — no new event-bus code is written here; this
is the estate's already-adopted NATS JetStream backbone
(`idp/platform/event-bus/nats.yaml`), used from Python with the official
client. LAW 43: the bus itself is not reinvented, only Otto's four streams
and subject taxonomy are new.

Retention: the spec's table (§4) reads "WorkQueue for `submitted`, Limits
for the rest" for the single OTTO_TASKS stream. JetStream retention is a
per-*stream* setting, not per-subject, and WorkQueue retention deletes a
message the moment any consumer acks it — which would make `otto replay`
unable to reconstruct a task's full state-transition history once a
consumer has processed the `submitted` event, directly contradicting P4
("if it isn't on the stream, it didn't happen") and the replay acceptance
test (spec §17 Phase 0). Decision made here, in one place: OTTO_TASKS runs
Limits retention like the other three streams (durable history for every
state), and the *queueing* behaviour the spec wants for dispatching
`submitted` events is provided by a durable pull consumer with explicit
ack (`Consumer.dispatch_queue` below) — a consumer group gives
work-queue-shaped delivery without deleting the stream's own history.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import nats
from nats.aio.client import Client as NATSClient
from nats.aio.msg import Msg
from nats.js import JetStreamContext
from nats.js.api import (
    AckPolicy,
    ConsumerConfig,
    DeliverPolicy,
    PubAck,
    RetentionPolicy,
    StorageType,
    StreamConfig,
)
from nats.js.errors import APIError, NotFoundError

from otto.spine import subjects


def default_servers() -> list[str]:
    # LAW 46: no hardcoded host. The estate's NATS lands at whatever
    # Service DNS or port-forward the caller is actually pointed at;
    # `nats://127.0.0.1:4222` is only the fallback for a bare local run.
    raw = os.environ.get("OTTO_NATS_URL", "nats://127.0.0.1:4222")
    return [s.strip() for s in raw.split(",") if s.strip()]


def default_duplicate_window_seconds() -> float:
    # `Nats-Msg-Id` dedupe (outbox.py, publish() above) only works inside
    # this window; leaving it at 0 means the server's own default (120s),
    # which is shorter than a real NATS-partition-then-heal cycle can take
    # (spec §17 network scenario) — a retry after the window has expired
    # would double-publish instead of no-op. Configurable, never a bare
    # constant (LAW 46); 2 hours is a deliberate choice, not the library's.
    return float(os.environ.get("OTTO_JETSTREAM_DUPLICATE_WINDOW_SECONDS", "7200"))


@dataclass(frozen=True)
class PublishResult:
    subject: str
    stream_seq: int
    duplicate: bool


class Bus:
    """One JetStream connection, stream management, publish and durable
    pull-consume. Every publish goes through `subjects.validate` and
    carries `Nats-Msg-Id`, so a message published twice (a retry after a
    partition heals, spec §17 network scenario) is stored once."""

    def __init__(self, servers: list[str] | None = None) -> None:
        self._servers = servers or default_servers()
        self._nc: NATSClient | None = None
        self._js: JetStreamContext | None = None

    async def connect(self) -> "Bus":
        self._nc = await nats.connect(servers=self._servers, connect_timeout=5)
        self._js = self._nc.jetstream()
        return self

    async def close(self) -> None:
        if self._nc is not None and not self._nc.is_closed:
            await self._nc.close()

    @property
    def js(self) -> JetStreamContext:
        if self._js is None:
            raise RuntimeError("Bus.connect() was not awaited")
        return self._js

    async def ensure_streams(self) -> list[str]:
        """Idempotent: create each of the four streams if absent, reconcile
        config if present. Safe to call on every process start (mirrors how
        the idp outbox relay expects its stream to already exist, except
        here Otto owns creating its own four rather than assuming a human
        applied a manifest first — there is no otto.yaml to apply yet)."""
        created = []
        for spec in subjects.STREAMS:
            cfg = StreamConfig(
                name=spec.name.value,
                subjects=list(spec.subjects),
                retention=RetentionPolicy.LIMITS,
                storage=StorageType.FILE,
                max_age=spec.retention_days * 24 * 3600,
                duplicate_window=default_duplicate_window_seconds(),
            )
            try:
                await self.js.add_stream(cfg)
                created.append(spec.name.value)
            except APIError:
                await self.js.update_stream(cfg)
        return created

    async def publish(
        self,
        subject: str,
        payload: bytes,
        *,
        task_id: str,
        seq: int,
        extra_headers: dict[str, str] | None = None,
    ) -> PublishResult:
        subjects.validate(subject)
        headers = {"Nats-Msg-Id": subjects.dedupe_id(task_id, seq)}
        if extra_headers:
            headers.update(extra_headers)
        ack: PubAck = await self.js.publish(subject, payload, headers=headers)
        return PublishResult(
            subject=subject, stream_seq=ack.seq, duplicate=bool(ack.duplicate)
        )

    async def durable_pull(
        self,
        *,
        stream: str,
        durable: str,
        filter_subject: str,
        deliver_all: bool = True,
    ):
        """A durable pull consumer, explicit-ack. This is the work-queue
        shape (each message goes to exactly one puller of a given durable
        name, redelivered until acked) without the stream itself losing
        history — see the retention note in the module docstring.

        nats-py's own `pull_subscribe` does NOT raise when a durable of
        this name already exists with different semantics — it silently
        binds to whatever is already there (confirmed live against
        nats-py 2.15.0; an earlier version of this method guarded that
        case inside an `except APIError`, which is dead code because no
        exception is ever raised). The check has to happen BEFORE
        binding: fetch the existing consumer's config first, compare the
        fields that change delivery *semantics*, and refuse the mismatch
        outright — never call `pull_subscribe` at all in that case."""
        cfg = ConsumerConfig(
            durable_name=durable,
            deliver_policy=DeliverPolicy.ALL if deliver_all else DeliverPolicy.NEW,
            ack_policy=AckPolicy.EXPLICIT,
            filter_subject=filter_subject,
        )
        try:
            info = await self.js.consumer_info(stream, durable)
        except NotFoundError:
            info = None

        if info is not None:
            existing = info.config
            mismatches = {
                field: (getattr(existing, field), wanted)
                for field, wanted in (
                    ("deliver_policy", cfg.deliver_policy),
                    ("ack_policy", cfg.ack_policy),
                    ("filter_subject", cfg.filter_subject),
                )
                if getattr(existing, field) != wanted
            }
            if mismatches:
                raise RuntimeError(
                    f"durable consumer {durable!r} on stream {stream!r} already "
                    f"exists with incompatible config: {mismatches}"
                )

        return await self.js.pull_subscribe(
            filter_subject, durable=durable, stream=stream, config=cfg
        )

    async def read_all(self, *, stream: str, filter_subject: str) -> list[Msg]:
        """Ordered, ephemeral, read-only sweep of everything currently on a
        subject filter — the read path `otto replay` uses. Ephemeral
        because replay never wants to compete with, or perturb, any
        durable consumer's delivery state; it only ever *reads*."""
        psub = await self.js.pull_subscribe(filter_subject, stream=stream)
        out: list[Msg] = []
        while True:
            try:
                batch = await psub.fetch(100, timeout=1)
            except TimeoutError:
                break
            if not batch:
                break
            for m in batch:
                out.append(m)
                await m.ack()
        try:
            await psub.unsubscribe()
        except APIError as exc:
            # Server may have already dropped the ephemeral consumer (e.g. it
            # expired between our last fetch and this call) — that is the
            # exact case this cleanup exists to tolerate. Narrowed to the
            # JetStream API error type, not swallowed blind, and logged so
            # an unexpected recurrence is visible instead of silent.
            logging.getLogger(__name__).debug("ephemeral unsubscribe no-op: %s", exc)
        return out
