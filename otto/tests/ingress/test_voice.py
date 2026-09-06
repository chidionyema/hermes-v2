"""Hands and senses, step 3 (voice): a voice note becomes words.

Spec ``docs/specs/otto-door-hands-and-senses.md``: a Telegram update with a
``voice`` object and a stub transcriber yields an envelope whose content is
the transcript and whose capabilities include ``VOICE_IN``; the worker with a
stub plugin calls both ``send_reply`` and ``send_voice``.

Everything here is offline. The enrichment ``TelegramMediaEnrichment`` is
stubbed so no socket opens and no fork tool imports; the transport is stubbed
in the plugin test so send_voice never leaves the process. ADR 0022 keeps the
real speech in the fork; this suite proves the seams the fork plugs into.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

from otto.ingress import worker as worker_module
from otto.ingress.gateway import ACCEPTED, NOTHING_TO_DO, EventGateway
from otto.ingress.media import (
    TelegramMediaEnrichment,
    detect_media,
    format_voice,
)
from otto.ingress.plugins import TELEGRAM, TelegramPlugin
from otto.ingress.store import ChannelBinding, SqliteChannelBindingStore
from otto.spine.envelope import TaskEnvelope, TaskSource

SECRET_REF = "vault://otto/acme/telegram"  # noqa: S105 - a reference, not a value
OUTBOUND_REF = "vault://otto/acme/telegram-bot"  # noqa: S105 - a reference, not a value
WEBHOOK_SECRET = "inbound-voice-secret"  # noqa: S105 - test fixture
BOT_TOKEN = "outbound-voice-bot-token"  # noqa: S105 - test fixture
TENANT = "acme"
CHAT_ID = 4242


def _voice_update(transcript_hint: bool = True) -> bytes:
    m = {
        "message": {
            "chat": {"id": CHAT_ID},
            "date": 1_725_000_000,
            # No "text": a voice note's only reachable content is speech, so
            # the normaliser would gate it as nothing-to-act-on unless the
            # gateway's media seam turns it into words first.
            "voice": {"file_id": "AwADvoice1", "file_size": 2323},
        }
    }
    if not transcript_hint:
        m["message"]["caption"] = None
    return json.dumps(m).encode("utf-8")


def _headers() -> dict[str, str]:
    return {"X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET}


# -- seam under test ------------------------------------------------------


def test_detect_media_reads_voice_off_the_wire() -> None:
    """Purely off JSON: a ``voice`` object yields a speech intent with the
    file id, and a text message yields none (``MediaIntent(None)``) so a
    text door behaves exactly as before."""
    intent = detect_media(json.loads(_voice_update())["message"])
    assert intent.kind == "voice"
    assert intent.file_id == "AwADvoice1"
    assert intent.is_speech
    from otto.ingress.media import MediaIntent

    assert detect_media({"text": "hi"}) == MediaIntent(None)


def test_the_voice_seam_formats_the_transcript() -> None:
    assert format_voice("the accent is lovely") == "[voice] the accent is lovely"


def test_the_telegram_surface_declares_voice_capabilities() -> None:
    """'capabilities include VOICE_IN': the capability-negotiated renderer
    reads the surface's declared set, so a speech reply is gated on this
    constant. It travels with every telegram envelope."""
    from otto.surface.bindings.telegram import TELEGRAM_CAPABILITIES
    from otto.surface.envelope import Capability

    assert Capability.VOICE_IN in TELEGRAM_CAPABILITIES
    assert Capability.VOICE_OUT in TELEGRAM_CAPABILITIES


# -- helper fakes (self-contained; the worker suite here grades the round
#    trip through the real gateway and the real worker with a stub plugin)


class _StubTranscriber:
    def __init__(self, transcript: str) -> None:
        self.transcript = transcript
        self.called_with: list[str] = []

    def transcribe(self, audio_path: str) -> str:
        self.called_with.append(str(audio_path))
        return self.transcript


class _StubFetcher:
    def download(self, bot_token: str, file_id: str, *, suffix: str):
        class _P:
            def __str__(self) -> str:
                # A test fake of the seam's download dir; never a real path.
                return f"/tmp/voice{self.suffix}"  # noqa: S108 - fake download dir

            @property
            def suffix(self) -> str:
                return suffix

        return _P()


def _stub_voice_enrichment(transcript: str) -> TelegramMediaEnrichment:
    return TelegramMediaEnrichment(
        fetcher=_StubFetcher(), transcriber=_StubTranscriber(transcript)
    )


class _Secrets:
    def __init__(self, values: dict[str, str]) -> None:
        self._values = values

    def resolve(self, secret_ref: str) -> str:
        from otto.ingress.secrets import SecretNotFound

        try:
            return self._values[secret_ref]
        except KeyError:
            raise SecretNotFound(secret_ref) from None


class _Publisher:
    def __init__(self) -> None:
        self.envelopes: list[TaskEnvelope] = []

    def publish_submitted(self, envelope: TaskEnvelope) -> str:
        self.envelopes.append(envelope)
        return "otto.task.v1.submitted"


class _Obs:
    def info(self, *args, **kwargs) -> None:
        return None

    def task_span(self, *args, **kwargs):
        from contextlib import nullcontext

        return nullcontext()


@dataclass
class _Msg:
    acked: bool = False
    naked: bool = False
    termed: bool = False
    data: bytes = b""

    async def ack(self) -> None:
        self.acked = True

    async def nak(self) -> None:
        self.naked = True

    async def term(self) -> None:
        self.termed = True


def _store(outbound=OUTBOUND_REF) -> SqliteChannelBindingStore:
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


@dataclass
class _RecordingPlugin:
    channel: str = TELEGRAM
    task_source: TaskSource = TaskSource.telegram
    sent_text: list[tuple[str, str, str]] = field(default_factory=list)
    sent_voice: list[tuple[str, str, str]] = field(default_factory=list)

    def send_reply(self, secret: str, reply_to: str, text: str) -> None:
        self.sent_text.append((secret, reply_to, text))

    def send_voice(self, secret: str, reply_to: str, text: str) -> None:
        self.sent_voice.append((secret, reply_to, text))


# -- the door turns a voice note into a spoken-word task ------------------


def test_a_voice_note_becomes_a_prefixed_transcribed_task() -> None:
    publisher = _Publisher()
    gateway = EventGateway(
        store=_store(),
        secrets=_Secrets({SECRET_REF: WEBHOOK_SECRET}),
        publisher=publisher,
        obs=_Obs(),
        media=_stub_voice_enrichment("the budget ran dry in march"),
    )
    result = gateway.handle(TELEGRAM, _headers(), _voice_update())

    assert result.status == ACCEPTED, result.reason
    envelope = publisher.envelopes[0]
    assert envelope.input == "[voice] the budget ran dry in march"
    # Step 3: the answering lane knows this inbound was speech.
    assert envelope.wants_voice_reply is True


def test_a_text_message_with_a_voice_seam_wired_stays_ordinary_text() -> None:
    """Wiring the media seam must not change text behaviour: a plain text
    update is still gated and published as text, not as media."""
    publisher = _Publisher()
    gateway = EventGateway(
        store=_store(),
        secrets=_Secrets({SECRET_REF: WEBHOOK_SECRET}),
        publisher=publisher,
        obs=_Obs(),
        media=_stub_voice_enrichment("n/a"),
    )
    body = json.dumps(
        {"message": {"chat": {"id": CHAT_ID}, "date": 1, "text": "just words"}}
    ).encode("utf-8")
    result = gateway.handle(TELEGRAM, _headers(), body)

    assert result.status == ACCEPTED
    assert publisher.envelopes[0].input == "just words"
    assert publisher.envelopes[0].wants_voice_reply is False


def test_a_voice_note_with_no_media_seam_is_still_nothing_to_do() -> None:
    """A text-only door must keep its old behaviour exactly: a bare voice
    note (no words of its own) is gated as nothing to act on, not crashed
    on, because it had no media seam to sense it."""
    publisher = _Publisher()
    gateway = EventGateway(
        store=_store(),
        secrets=_Secrets({SECRET_REF: WEBHOOK_SECRET}),
        publisher=publisher,
        obs=_Obs(),
    )
    result = gateway.handle(TELEGRAM, _headers(), _voice_update())

    assert result.status == NOTHING_TO_DO
    assert publisher.envelopes == []


# -- the worker answers a voice note with both text and audio -------------


def test_the_worker_calls_both_send_reply_and_send_voice(monkeypatch) -> None:
    """Step 3's outbound half: for an inbound that wants a voice reply, the
    text answer is delivered and the plugin is also asked to speak it."""

    class _Answer:
        def __init__(self, text: str) -> None:
            self.text = text

        @property
        def reply_text(self) -> str:
            return self.text

    publisher = _Publisher()
    gateway = EventGateway(
        store=_store(),
        secrets=_Secrets({SECRET_REF: WEBHOOK_SECRET}),
        publisher=publisher,
        obs=_Obs(),
        media=_stub_voice_enrichment("let us look at the numbers"),
    )
    accepted = gateway.handle(TELEGRAM, _headers(), _voice_update())
    assert accepted.status == ACCEPTED
    envelope = publisher.envelopes[0]

    plugin = _RecordingPlugin()
    worker = worker_module.Worker(
        bus=None,
        store=_store(),
        secrets=_Secrets({OUTBOUND_REF: BOT_TOKEN}),
        obs=_Obs(),
        lanes=None,
        gateway=None,
        plugins={TELEGRAM: plugin},
    )
    msg = _Msg(data=envelope.canonical_json())
    monkeypatch.setattr(
        worker_module,
        "answer_envelope",
        lambda *a, **k: _Answer("six point nine cores, and it is enough"),
    )
    asyncio.run(worker._handle(msg))

    assert plugin.sent_text == [
        (BOT_TOKEN, str(CHAT_ID), "six point nine cores, and it is enough")
    ]
    assert plugin.sent_voice == [
        (BOT_TOKEN, str(CHAT_ID), "six point nine cores, and it is enough")
    ]
    assert msg.acked and not msg.naked and not msg.termed


def test_an_ordinary_text_envelope_never_asks_for_a_voice_reply() -> None:
    """wants_voice_reply is off by default on the envelope, so a door that
    never set it answers exactly as the step-2 worker did."""
    env = TaskEnvelope.new(
        tenant_id=TENANT,
        source=TaskSource.telegram,
        task_class=__import__(
            "otto.spine.envelope", fromlist=["TaskClass"]
        ).TaskClass.comms,
        input="plain text",
        authority_ceiling=__import__("otto.spine.envelope", fromlist=["Tier"]).Tier.T2,
        provenance="ot",
        reply_to=str(CHAT_ID),
    )
    assert env.wants_voice_reply is False


def test_telegram_plugin_speaks_when_a_speaker_is_wired(monkeypatch) -> None:
    """The plugin only sends audio when it has a synthesizer; with one, it
    uploads the produced voice note through the bot API."""

    class _FakeSpeaker:
        def synthesize(self, text: str) -> bytes:
            return b"OGG-DATA:" + text.encode("utf-8")

    uploaded: list[tuple[str, int, bytes]] = []

    class _FakeTransport:
        def __init__(self, token: str) -> None:
            self.token = token

        def send_voice(self, chat_id: int, audio: bytes) -> None:
            uploaded.append((self.token, chat_id, audio))

        def send_message(self, chat_id: int, text: str) -> None:  # pragma: no cover
            pass

    monkeypatch.setattr(
        "otto.ingress.plugins.TelegramHTTPTransport",
        lambda token: _FakeTransport(token),
    )
    plugin = TelegramPlugin(speaker=_FakeSpeaker())
    plugin.send_voice(BOT_TOKEN, str(CHAT_ID), "six point nine")

    assert uploaded == [(BOT_TOKEN, CHAT_ID, b"OGG-DATA:six point nine")]


def test_telegram_plugin_without_a_speaker_stays_silent_and_safe() -> None:
    """No synthesizer is a supported state (a bare checkout, a door with no
    audio); the plugin must not fail or crash, because the text answer has
    already been delivered."""
    TelegramPlugin().send_voice(BOT_TOKEN, str(CHAT_ID), "six point nine")
