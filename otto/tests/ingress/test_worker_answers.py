"""The door answers back.

Before these tests the gateway accepted an event from any channel, put a
task on the bus, and that was the end of it: nothing subscribed, and the
envelope carried no address to answer on. So the tests here grade the
round trip rather than either half of it — an update arrives shaped the
way Telegram sends one, and the customer's own bot token is what the
reply is sent with.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field

import pytest

from otto.boot.pipeline import AnswerOutcome
from otto.ingress import worker as worker_module
from otto.ingress.gateway import ACCEPTED, EventGateway
from otto.ingress.plugins import TELEGRAM, OutboundNotSupported, TelegramPlugin
from otto.ingress.store import ChannelBinding, SqliteChannelBindingStore
from otto.spine.envelope import TaskEnvelope, TaskSource

WEBHOOK_SECRET = "inbound-shared-secret"
BOT_TOKEN = "outbound-bot-token"
SECRET_REF = "vault://otto/acme/telegram"
OUTBOUND_REF = "vault://otto/acme/telegram-bot"
TENANT = "acme"
CHAT_ID = 4242

UPDATE = {
    "message": {
        "chat": {"id": CHAT_ID},
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


class SilentObs:
    """The worker logs through an ObsHandle; nothing here grades logging."""

    def info(self, *args, **kwargs) -> None:
        return None

    def task_span(self, *args, **kwargs):
        from contextlib import nullcontext

        return nullcontext()


@dataclass
class FakeMsg:
    data: bytes
    acked: bool = False
    naked: bool = False
    termed: bool = False

    async def ack(self) -> None:
        self.acked = True

    async def nak(self) -> None:
        self.naked = True

    async def term(self) -> None:
        self.termed = True


@dataclass
class RecordingPlugin:
    channel: str = TELEGRAM
    task_source: TaskSource = TaskSource.telegram
    sent: list[tuple[str, str, str]] = field(default_factory=list)
    raises: Exception | None = None

    def send_reply(self, secret: str, reply_to: str, text: str) -> None:
        if self.raises is not None:
            raise self.raises
        self.sent.append((secret, reply_to, text))


def _store(outbound: str | None = OUTBOUND_REF) -> SqliteChannelBindingStore:
    store = SqliteChannelBindingStore()
    store.register(
        ChannelBinding(
            tenant_id=TENANT,
            channel=TELEGRAM,
            external_id="acme-workspace",
            secret_ref=SECRET_REF,
            outbound_secret_ref=outbound,
        ),
        credential=WEBHOOK_SECRET,
    )
    return store


def _accepted_envelope() -> TaskEnvelope:
    """Push a real Telegram-shaped update through the real gateway and
    take the envelope it published — the worker is then fed exactly what
    the door produces, not a hand-built envelope that could drift."""
    import json

    publisher = RecordingPublisher()
    gateway = EventGateway(
        store=_store(),
        secrets=FakeSecrets({SECRET_REF: WEBHOOK_SECRET}),
        publisher=publisher,
        obs=SilentObs(),
    )
    result = gateway.handle(
        TELEGRAM,
        {"X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET},
        json.dumps(UPDATE).encode("utf-8"),
    )
    assert result.status == ACCEPTED, result.reason
    return publisher.envelopes[0]


def _worker(store, secrets, plugin, *, answer: AnswerOutcome | Exception):
    def fake_answer_envelope(envelope, **kwargs):
        if isinstance(answer, Exception):
            raise answer
        return answer

    return (
        worker_module.Worker(
            bus=None,
            store=store,
            secrets=secrets,
            obs=SilentObs(),
            lanes=None,
            gateway=None,
            plugins={TELEGRAM: plugin},
        ),
        fake_answer_envelope,
    )


def _run(worker, msg, fake_answer, monkeypatch) -> None:
    monkeypatch.setattr(worker_module, "answer_envelope", fake_answer)
    asyncio.run(worker._handle(msg))


def _answer(text: str | None) -> AnswerOutcome:
    return AnswerOutcome(
        gateway_response=None, router_response=None, fact=None, reply_text=text
    )


def test_the_accepted_task_carries_the_address_to_answer_on() -> None:
    """The chat the message came from survives the trip through the
    surface binding and onto the envelope that goes on the bus."""
    envelope = _accepted_envelope()
    assert envelope.reply_to == str(CHAT_ID)
    assert envelope.tenant_id == TENANT
    assert envelope.source is TaskSource.telegram


def test_a_task_off_the_bus_is_answered_with_the_customers_own_token(
    monkeypatch,
) -> None:
    """The whole round trip: the door's envelope, answered, and sent back
    with the credential from that customer's binding row — not with any
    token this deployment holds in its own environment."""
    envelope = _accepted_envelope()
    plugin = RecordingPlugin()
    worker, fake = _worker(
        _store(),
        FakeSecrets({OUTBOUND_REF: BOT_TOKEN}),
        plugin,
        answer=_answer("6.9 cores, and 6.90 of them are asked for."),
    )
    msg = FakeMsg(data=envelope.canonical_json())

    _run(worker, msg, fake, monkeypatch)

    assert plugin.sent == [
        (BOT_TOKEN, str(CHAT_ID), "6.9 cores, and 6.90 of them are asked for.")
    ]
    assert msg.acked and not msg.naked and not msg.termed


def test_a_task_with_no_reply_address_is_terminated_not_retried(
    monkeypatch,
) -> None:
    """A cron tick or a subtask has nobody to answer. Redelivering it
    forever would block every real task queued behind it."""
    envelope = TaskEnvelope.new(
        tenant_id=TENANT,
        source=TaskSource.cron,
        task_class=__import__(
            "otto.spine.envelope", fromlist=["TaskClass"]
        ).TaskClass.comms,
        input="nightly sweep",
        authority_ceiling=__import__("otto.spine.envelope", fromlist=["Tier"]).Tier.T1,
        provenance="cron",
    )
    plugin = RecordingPlugin()
    worker, fake = _worker(
        _store(), FakeSecrets({OUTBOUND_REF: BOT_TOKEN}), plugin, answer=_answer("hi")
    )
    msg = FakeMsg(data=envelope.canonical_json())

    _run(worker, msg, fake, monkeypatch)

    assert msg.termed and not msg.acked and not msg.naked
    assert plugin.sent == []


def test_a_listen_only_binding_is_terminated_with_no_send(monkeypatch) -> None:
    """A row with no outbound reference is a legitimate state — the
    customer connected a channel this platform only listens on. It is
    not a delivery to retry until an operator edits the row."""
    envelope = _accepted_envelope()
    plugin = RecordingPlugin()
    worker, fake = _worker(
        _store(outbound=None),
        FakeSecrets({OUTBOUND_REF: BOT_TOKEN}),
        plugin,
        answer=_answer("an answer nobody can be sent"),
    )
    msg = FakeMsg(data=envelope.canonical_json())

    _run(worker, msg, fake, monkeypatch)

    assert msg.termed and plugin.sent == []


def test_a_denied_task_is_acked_and_answers_nothing(monkeypatch) -> None:
    """Silence is the designed answer to a sender the gateway denied, and
    the task was still handled — so it is acknowledged, not retried."""
    envelope = _accepted_envelope()
    plugin = RecordingPlugin()
    worker, fake = _worker(
        _store(), FakeSecrets({OUTBOUND_REF: BOT_TOKEN}), plugin, answer=_answer(None)
    )
    msg = FakeMsg(data=envelope.canonical_json())

    _run(worker, msg, fake, monkeypatch)

    assert msg.acked and plugin.sent == []


def test_a_failed_send_is_negatively_acknowledged(monkeypatch) -> None:
    """Telegram refusing one delivery is transient; the customer is still
    owed the answer, so the server redelivers it."""
    envelope = _accepted_envelope()
    plugin = RecordingPlugin(raises=RuntimeError("sendMessage: HTTP 502"))
    worker, fake = _worker(
        _store(),
        FakeSecrets({OUTBOUND_REF: BOT_TOKEN}),
        plugin,
        answer=_answer("an answer worth retrying"),
    )
    msg = FakeMsg(data=envelope.canonical_json())

    _run(worker, msg, fake, monkeypatch)

    assert msg.naked and not msg.acked and not msg.termed


def test_a_missing_outbound_secret_is_negatively_acknowledged(monkeypatch) -> None:
    """The row names a reference the secret store has not projected yet.
    That is the platform's fault and it is fixable, so the task waits."""
    envelope = _accepted_envelope()
    plugin = RecordingPlugin()
    worker, fake = _worker(
        _store(), FakeSecrets({}), plugin, answer=_answer("held until the secret lands")
    )
    msg = FakeMsg(data=envelope.canonical_json())

    _run(worker, msg, fake, monkeypatch)

    assert msg.naked and plugin.sent == []


def test_bytes_that_are_not_an_envelope_are_terminated(monkeypatch) -> None:
    """They will not parse on a second delivery either."""
    plugin = RecordingPlugin()
    worker, fake = _worker(
        _store(), FakeSecrets({OUTBOUND_REF: BOT_TOKEN}), plugin, answer=_answer("x")
    )
    msg = FakeMsg(data=b"not an envelope")

    _run(worker, msg, fake, monkeypatch)

    assert msg.termed


def test_the_http_channel_refuses_to_push_an_answer() -> None:
    """A plain HTTP caller has no address to push to; the plugin says so
    rather than the worker learning what each channel can do."""
    from otto.ingress.plugins import HttpPlugin

    with pytest.raises(OutboundNotSupported):
        HttpPlugin().send_reply("secret", "somewhere", "an answer")


def test_the_telegram_plugin_sends_through_the_bot_api(monkeypatch) -> None:
    """The outbound secret is used as the bot token and the opaque
    address is the chat id — the one place either is interpreted."""
    sent: list[tuple[str, int, str]] = []

    class FakeTransport:
        def __init__(self, token: str) -> None:
            self.token = token

        def send_message(self, chat_id: int, text: str) -> None:
            sent.append((self.token, chat_id, text))

    monkeypatch.setattr(
        "otto.ingress.plugins.TelegramHTTPTransport",
        lambda token: FakeTransport(token),
    )
    TelegramPlugin().send_reply(BOT_TOKEN, str(CHAT_ID), "answered")
    assert sent == [(BOT_TOKEN, CHAT_ID, "answered")]


SECOND_WEBHOOK_SECRET = "alerts-inbound-shared-secret"
SECOND_BOT_TOKEN = "alerts-outbound-bot-token"
SECOND_SECRET_REF = "vault://otto/acme/alerts"
SECOND_OUTBOUND_REF = "vault://otto/acme/alerts-bot"


def test_two_bots_on_one_tenant_each_answer_as_themselves(monkeypatch) -> None:
    """Founder 2026-09-05: both Telegram bots must answer. A message that
    came in on the second bot's webhook secret goes back out with the
    second bot's token, not with whichever row the tenant lookup finds
    first."""
    import json

    store = _store()
    store.register(
        ChannelBinding(
            tenant_id=TENANT,
            channel=TELEGRAM,
            external_id="alerts-bot:acme-workspace",
            secret_ref=SECOND_SECRET_REF,
            outbound_secret_ref=SECOND_OUTBOUND_REF,
        ),
        credential=SECOND_WEBHOOK_SECRET,
    )
    publisher = RecordingPublisher()
    gateway = EventGateway(
        store=store,
        secrets=FakeSecrets(
            {SECRET_REF: WEBHOOK_SECRET, SECOND_SECRET_REF: SECOND_WEBHOOK_SECRET}
        ),
        publisher=publisher,
        obs=SilentObs(),
    )
    result = gateway.handle(
        TELEGRAM,
        {"X-Telegram-Bot-Api-Secret-Token": SECOND_WEBHOOK_SECRET},
        json.dumps(UPDATE).encode("utf-8"),
    )
    assert result.status == ACCEPTED, result.reason
    envelope = publisher.envelopes[0]
    assert envelope.reply_binding == "alerts-bot:acme-workspace"

    plugin = RecordingPlugin()
    worker, fake = _worker(
        store,
        FakeSecrets({OUTBOUND_REF: BOT_TOKEN, SECOND_OUTBOUND_REF: SECOND_BOT_TOKEN}),
        plugin,
        answer=_answer("from the second bot"),
    )
    msg = FakeMsg(data=envelope.canonical_json())

    _run(worker, msg, fake, monkeypatch)

    assert plugin.sent == [(SECOND_BOT_TOKEN, str(CHAT_ID), "from the second bot")]
    assert msg.acked and not msg.naked and not msg.termed


def test_an_envelope_naming_no_binding_still_answers_by_tenant(monkeypatch) -> None:
    """A task minted before the door stamped the binding is answered the
    old way, not dropped."""
    envelope = _accepted_envelope().model_copy(update={"reply_binding": None})
    plugin = RecordingPlugin()
    worker, fake = _worker(
        _store(), FakeSecrets({OUTBOUND_REF: BOT_TOKEN}), plugin, answer=_answer("ok")
    )
    msg = FakeMsg(data=envelope.canonical_json())

    _run(worker, msg, fake, monkeypatch)

    assert plugin.sent == [(BOT_TOKEN, str(CHAT_ID), "ok")]


@dataclass
class TypingPlugin(RecordingPlugin):
    """A Telegram-shaped plugin: it can also show "typing"."""

    actions: list[tuple[str, str]] = field(default_factory=list)
    first_action: threading.Event = field(default_factory=threading.Event)

    def send_chat_action(self, secret: str, reply_to: str) -> None:
        self.actions.append((secret, reply_to))
        self.first_action.set()


def test_the_typing_indicator_shows_while_the_answer_is_being_written(
    monkeypatch,
) -> None:
    """The founder could not tell whether Otto was answering or down
    (2026-09-06): the boot path showed Telegram's typing indicator, the bus
    path every real message takes showed nothing. The indicator is sent with
    the customer's own token to the chat being answered, before the answer
    exists, and stops once the reply is out."""
    envelope = _accepted_envelope()
    plugin = TypingPlugin()
    worker, _unused = _worker(
        _store(), FakeSecrets({OUTBOUND_REF: BOT_TOKEN}), plugin, answer=_answer("x")
    )

    def slow_answer(envelope, **kwargs):
        # The model is "thinking": the indicator must already be on screen.
        assert plugin.first_action.wait(2.0), "no typing indicator while answering"
        return _answer("6.9 cores.")

    msg = FakeMsg(data=envelope.canonical_json())
    _run(worker, msg, slow_answer, monkeypatch)

    assert plugin.actions[0] == (BOT_TOKEN, str(CHAT_ID))
    assert plugin.sent == [(BOT_TOKEN, str(CHAT_ID), "6.9 cores.")]
    sent_after = len(plugin.actions)
    assert not any(
        t.name == "otto-typing" and t.is_alive() for t in threading.enumerate()
    )
    assert len(plugin.actions) == sent_after


def test_a_refused_typing_indicator_never_costs_the_answer(monkeypatch) -> None:
    envelope = _accepted_envelope()
    plugin = TypingPlugin()

    def refuse(secret: str, reply_to: str) -> None:
        plugin.first_action.set()
        raise RuntimeError("telegram: 429")

    plugin.send_chat_action = refuse  # type: ignore[method-assign]
    worker, fake = _worker(
        _store(),
        FakeSecrets({OUTBOUND_REF: BOT_TOKEN}),
        plugin,
        answer=_answer("still here"),
    )
    msg = FakeMsg(data=envelope.canonical_json())

    _run(worker, msg, fake, monkeypatch)

    assert plugin.first_action.is_set()
    assert plugin.sent == [(BOT_TOKEN, str(CHAT_ID), "still here")]
