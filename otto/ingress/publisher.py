"""Handing a normalised task to the spine.

The gateway's job ends the moment a task envelope exists and is on the
bus; everything after that is the agent lanes' work, reading from
JetStream. That handover is one method, behind a Protocol, for two
reasons: the tests bind a recorder and never need a NATS server, and the
gateway itself stays free of the async machinery ``otto.spine.bus``
requires.

Subject and stream are not re-decided here — ``otto.spine.subjects``
already owns the taxonomy, and this module calls it rather than
formatting a subject string of its own.
"""

from __future__ import annotations

import asyncio
from typing import Protocol

from otto.spine import subjects
from otto.spine.bus import Bus
from otto.spine.envelope import TaskEnvelope


class EventPublisher(Protocol):
    def publish_submitted(self, envelope: TaskEnvelope) -> str:
        """Put a freshly submitted task on the bus. Returns the subject it
        was published to, so a caller can record where the work went."""
        ...


class JetStreamPublisher:
    """The real publisher: ``otto.spine.bus.Bus`` over NATS JetStream.

    ``Bus`` is asynchronous because ``nats-py`` is; the gateway's request
    handling is synchronous because ``http.server`` is. Rather than make
    the whole gateway async for one call, the bridge is confined to this
    class, which owns a loop and runs the coroutine on it. A future
    asynchronous server implements ``EventPublisher`` directly and this
    class is deleted, with nothing above it changing.
    """

    def __init__(self, bus: Bus, loop: asyncio.AbstractEventLoop | None = None) -> None:
        self._bus = bus
        self._loop = loop or asyncio.new_event_loop()

    def publish_submitted(self, envelope: TaskEnvelope) -> str:
        subject = subjects.task_subject(subjects.TaskState.submitted)
        try:
            return self._publish(subject, envelope)
        except Exception:
            # The connection opened at boot can die while the process stays
            # up, and nothing above here notices: readiness was a process
            # probe, so the pod stayed Ready while every accepted turn was
            # dropped. Measured 2026-10-23 — the last task on OTTO_TASKS was
            # published 2026-10-19T12:08:26Z and the door had been answering
            # 202 with nothing behind it ever since.
            #
            # One reconnect, then one retry: the envelope carries
            # ``Nats-Msg-Id``, so a retry of a publish that actually landed
            # is stored once, and the dedupe window covers this case rather
            # than hoping the first attempt failed cleanly.
            self._loop.run_until_complete(self._bus.reconnect())
            return self._publish(subject, envelope)

    def _publish(self, subject: str, envelope: TaskEnvelope) -> str:
        self._loop.run_until_complete(
            self._bus.publish(
                subject,
                envelope.canonical_json(),
                task_id=envelope.task_id,
                seq=0,
                # The tenant rides in a header as well as in the payload so
                # a consumer can filter without decoding every message, and
                # so an operator reading the stream sees whose work it is.
                extra_headers={"Otto-Tenant-Id": envelope.tenant_id},
            )
        )
        return subject
