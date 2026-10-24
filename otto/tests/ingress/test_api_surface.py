"""The API surface: accepted, answered, and readable back by task id.

Measured 2026-10-23, against the real door: ``POST /webhook/api`` answered
202 with a task id, and the turn was dropped one line later with
``worker.no_reply_address`` — an ``api`` caller has no chat to push an
answer to, so the worker treated "no address" as "nobody to answer". The
same turn on the same pod also proved the second half of the defect is
invisible from outside: the gateway keeps its JetStream connection, the
connection dies, readiness stays green, and every accepted turn is dropped
after that without a line in the log.

So the tests here grade the three things a caller can be lied to about:

* a publish that fails is a 503, never a 202 (``publish_failed``);
* a pullable surface is answered rather than terminated;
* the answer can be fetched by the task id the caller was given, by the
  tenant that asked and nobody else.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

from otto.ingress import server as server_module
from otto.ingress.gateway import (
    ACCEPTED,
    NOT_FOUND,
    UNAUTHORIZED,
    UNAVAILABLE,
    EventGateway,
)
from otto.ingress.plugins import TELEGRAM
from otto.ingress.publisher import JetStreamPublisher
from otto.ingress.store import ChannelBinding, SqliteChannelBindingStore
from otto.memory.conversation import StoreUnavailable, Turn
from otto.spine.envelope import TaskClass, TaskEnvelope, TaskSource, Tier

TENANT = "acme"
OTHER_TENANT = "globex"
WEBHOOK_SECRET = "inbound-shared-secret"
SECRET_REF = "vault://otto/acme/telegram"
TASK_ID = "01M5MQE4E5H2R700W22DRWP799"

UPDATE = {
    "message": {
        "chat": {"id": 4242},
        "date": 1_725_000_000,
        "text": "how many nodes are we paying for",
    }
}


class FakeSecrets:
    def __init__(self, values: dict[str, str]) -> None:
        self._values = values

    def resolve(self, secret_ref: str) -> str:
        from otto.ingress.secrets import SecretNotFound

        try:
            return self._values[secret_ref]
        except KeyError:
            raise SecretNotFound(secret_ref) from None


class RecordingPublisher:
    def __init__(self) -> None:
        self.envelopes: list[TaskEnvelope] = []

    def publish_submitted(self, envelope: TaskEnvelope) -> str:
        self.envelopes.append(envelope)
        return "otto.task.v1.submitted"


class RefusingPublisher:
    """The bus is gone. This is what the door saw for four days."""

    def publish_submitted(self, envelope: TaskEnvelope) -> str:
        raise ConnectionError("nats: connection closed")


@dataclass
class RecordingObs:
    lines: list[tuple[str, dict]] = field(default_factory=list)

    def info(self, name: str, ctx=None, **kwargs) -> None:
        self.lines.append((name, kwargs))

    def names(self) -> list[str]:
        return [name for name, _ in self.lines]

    def task_span(self, *args, **kwargs):
        from contextlib import nullcontext

        return nullcontext()


def _store() -> SqliteChannelBindingStore:
    store = SqliteChannelBindingStore()
    store.register(
        ChannelBinding(
            tenant_id=TENANT,
            channel=TELEGRAM,
            external_id="acme-workspace",
            secret_ref=SECRET_REF,
            outbound_secret_ref="vault://otto/acme/telegram-bot",
        ),
        credential=WEBHOOK_SECRET,
    )
    return store


def _gateway(publisher, *, secrets: dict[str, str] | None = None) -> EventGateway:
    return EventGateway(
        store=_store(),
        secrets=FakeSecrets(secrets or {SECRET_REF: WEBHOOK_SECRET}),
        publisher=publisher,
        obs=RecordingObs(),
    )


def _headers() -> dict[str, str]:
    return {"X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET}


def _turn(tenant_id: str = TENANT) -> Turn:
    import datetime as dt

    return Turn(
        task_ulid=TASK_ID,
        tenant_id=tenant_id,
        surface="api",
        asked_at=dt.datetime(2026, 10, 23, 12, 8, 26, tzinfo=dt.timezone.utc),
        asked="how many nodes are we paying for",
        answered="6.9 cores, and 6.90 of them are asked for.",
        lane="fast",
        model="minimax",
        attempts=1,
        outcome_state="answered",
        verified=True,
        claims_total=1,
        claims_clean=1,
        taint_capped=False,
        cost_usd=0.0011,
    )


# --- the accept path -----------------------------------------------------


def test_a_refused_publish_is_not_an_accept() -> None:
    """The lie that cost four days: 202 with nothing behind it. A caller
    that gets 202 has been told the work is queued; if it is not, the
    honest answer is 503 and a retry."""
    gateway = _gateway(RefusingPublisher())

    result = gateway.handle(TELEGRAM, _headers(), json.dumps(UPDATE).encode())

    assert result.status == UNAVAILABLE
    assert "not accepted" in result.reason


def test_the_refusal_is_named_in_the_log() -> None:
    """An outage nobody wrote down is the same as no outage as far as the
    next five days are concerned."""
    obs = RecordingObs()
    gateway = EventGateway(
        store=_store(),
        secrets=FakeSecrets({SECRET_REF: WEBHOOK_SECRET}),
        publisher=RefusingPublisher(),
        obs=obs,
    )

    gateway.handle(TELEGRAM, _headers(), json.dumps(UPDATE).encode())

    names = obs.names()
    assert "ingress.publish_failed" in names
    line = next(kw for name, kw in obs.lines if name == "ingress.publish_failed")
    assert line["channel"] == TELEGRAM
    assert line["error"] == "ConnectionError"


def test_a_dead_bus_is_reconnected_and_the_publish_retried() -> None:
    """The connection dies while the process lives. One reconnect and one
    retry, because the envelope carries Nats-Msg-Id and a retry of a
    publish that did land is stored once."""

    class FlakyBus:
        def __init__(self) -> None:
            self.published: list[tuple[str, bytes, str]] = []
            self.reconnects = 0
            self.fail_first = True

        async def publish(self, subject, body, *, task_id, seq, extra_headers):
            if self.fail_first:
                self.fail_first = False
                raise ConnectionError("nats: connection closed")
            self.published.append((subject, body, task_id))

        async def reconnect(self):
            self.reconnects += 1
            return self

    bus = FlakyBus()
    loop = asyncio.new_event_loop()
    publisher = JetStreamPublisher(bus, loop)
    envelope = TaskEnvelope.new(
        tenant_id=TENANT,
        source=TaskSource.api,
        task_class=TaskClass.comms,
        input="how many nodes are we paying for",
        authority_ceiling=Tier.T1,
        provenance="api",
    )

    try:
        subject = publisher.publish_submitted(envelope)
    finally:
        loop.close()

    assert bus.reconnects == 1
    assert [pub[2] for pub in bus.published] == [envelope.task_id]
    assert subject.startswith("otto.task")


def test_the_gateway_still_accepts_when_the_bus_is_up() -> None:
    publisher = RecordingPublisher()
    gateway = _gateway(publisher)

    result = gateway.handle(TELEGRAM, _headers(), json.dumps(UPDATE).encode())

    assert result.status == ACCEPTED
    assert publisher.envelopes[0].task_id


# --- the read-back -------------------------------------------------------


def test_the_answer_is_read_back_by_task_id(monkeypatch) -> None:
    monkeypatch.setattr(
        "otto.ingress.gateway.conversation.read_turn",
        lambda task_id, config=None: _turn() if task_id == TASK_ID else None,
    )
    gateway = _gateway(RecordingPublisher())

    result = gateway.answer_for(_headers(), TASK_ID)

    assert result.status == 200
    body = json.loads(result.body)
    assert body["answered"] == _turn().answered
    assert body["task_id"] == TASK_ID
    assert body["verified"] is True


def test_a_turn_is_not_readable_by_another_tenant(monkeypatch) -> None:
    """Absent, not forbidden: the reader already proved which tenant it is,
    and confirming that someone else's ULID exists would leak the customer
    list one task at a time."""
    monkeypatch.setattr(
        "otto.ingress.gateway.conversation.read_turn",
        lambda task_id, config=None: _turn(OTHER_TENANT),
    )
    gateway = _gateway(RecordingPublisher())

    result = gateway.answer_for(_headers(), TASK_ID)

    assert result.status == NOT_FOUND


def test_a_read_back_with_no_credential_is_refused() -> None:
    gateway = _gateway(RecordingPublisher())

    result = gateway.answer_for({}, TASK_ID)

    assert result.status == UNAUTHORIZED


def test_an_unknown_task_id_is_absent_not_an_error(monkeypatch) -> None:
    """A caller polls this immediately after its 202, so the first reads
    legitimately miss."""
    monkeypatch.setattr(
        "otto.ingress.gateway.conversation.read_turn",
        lambda task_id, config=None: None,
    )
    gateway = _gateway(RecordingPublisher())

    assert gateway.answer_for(_headers(), TASK_ID).status == NOT_FOUND


def test_an_unreachable_store_is_a_503_not_a_404(monkeypatch) -> None:
    """The difference matters: 404 tells the caller the answer does not
    exist and to stop asking; 503 tells it to come back."""

    def _boom(task_id, config=None):
        raise StoreUnavailable("no conversation store is configured")

    monkeypatch.setattr("otto.ingress.gateway.conversation.read_turn", _boom)
    gateway = _gateway(RecordingPublisher())

    assert gateway.answer_for(_headers(), TASK_ID).status == UNAVAILABLE


# --- the socket ----------------------------------------------------------


def test_a_read_back_path_yields_the_task_id() -> None:
    assert server_module.task_id_from_path(f"/turns/{TASK_ID}") == TASK_ID
    assert server_module.task_id_from_path(f"/turns/{TASK_ID}?wait=1") == TASK_ID
    assert server_module.task_id_from_path("/turns/") is None
    assert server_module.task_id_from_path(f"/turns/{TASK_ID}/extra") is None
    assert server_module.task_id_from_path("/healthz") is None
