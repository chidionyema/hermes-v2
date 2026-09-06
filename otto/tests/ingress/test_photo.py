"""Hands and senses, step 4 (photo): pixels become words.

Spec ``docs/specs/otto-door-hands-and-senses.md``: ``IMAGE_IN`` admission.
A photo a customer sends (with or without a caption) reaches the router and
memory as text — ``[image] <caption>\\n<description>`` — produced in the
image-describer seam. Everything here is offline: the describer is a stub and
no fork vision tool imports.
"""

from __future__ import annotations

import json

from otto.ingress.gateway import ACCEPTED, NOTHING_TO_DO, EventGateway
from otto.ingress.media import (
    TelegramMediaEnrichment,
    detect_media,
    format_image,
)
from otto.ingress.plugins import TELEGRAM
from otto.ingress.store import ChannelBinding, SqliteChannelBindingStore

SECRET_REF = "vault://otto/acme/telegram"  # noqa: S105 - a reference, not a value
WEBHOOK_SECRET = "inbound-photo-secret"  # noqa: S105 - test fixture
TENANT = "acme"
CHAT_ID = 9776


def _photo_message(caption: str | None = None) -> dict:
    m = {
        "chat": {"id": CHAT_ID},
        "date": 1_725_000_000,
        # Telegram sends every photo as several sizes; the largest carries
        # the most pixels and is the one worth describing.
        "photo": [
            {"file_id": "small", "width": 10, "height": 10, "file_size": 100},
            {"file_id": "fullres", "width": 1280, "height": 720, "file_size": 40_000},
        ],
    }
    if caption is not None:
        m["caption"] = caption
    return {"message": m}


def _photo_body(caption: str | None = None) -> bytes:
    return json.dumps(_photo_message(caption)).encode("utf-8")


def _headers() -> dict[str, str]:
    return {"X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET}


class _StubFetcher:
    def download(self, bot_token: str, file_id: str, *, suffix: str):
        class _P:
            def __init__(self, file_id: str) -> None:
                self._id = file_id

            def __str__(self) -> str:
                # A test fake of the seam's download dir; never a real path.
                return f"/tmp/photo-{self._id}.jpg"  # noqa: S108 - fake download dir

        return _P(file_id=file_id)


class _StubDescriber:
    def __init__(self, description: str, *, calls: list) -> None:
        self.description = description
        self.calls = calls

    def describe(self, image_path: str, question: str) -> str:
        self.calls.append((str(image_path), question))
        return self.description


def _photon_seam(desc: str, *, calls: list) -> TelegramMediaEnrichment:
    """A real seam whose fetcher writes nothing and whose describer returns
    known words — the photo path through ``process`` runs end to end, from
    intent to formatted content, with no fork import and no socket."""
    return TelegramMediaEnrichment(
        fetcher=_StubFetcher(), describer=_StubDescriber(desc, calls=calls)
    )


class _Publisher:
    def __init__(self) -> None:
        self.envelopes = []

    def publish_submitted(self, envelope) -> str:
        self.envelopes.append(envelope)
        return "otto.task.v1.submitted"


class _Obs:
    def info(self, *a, **k) -> None:
        return None

    def task_span(self, *a, **k):
        from contextlib import nullcontext

        return nullcontext()


class _Secrets:
    def resolve(self, secret_ref: str) -> str:
        if secret_ref == SECRET_REF:
            return WEBHOOK_SECRET
        from otto.ingress.secrets import SecretNotFound

        raise SecretNotFound(secret_ref)


def _store() -> SqliteChannelBindingStore:
    store = SqliteChannelBindingStore()
    store.register(
        ChannelBinding(
            tenant_id=TENANT,
            channel=TELEGRAM,
            external_id="acme-photo-bot",
            secret_ref=SECRET_REF,
        ),
        credential=WEBHOOK_SECRET,
    )
    return store


# -- words from pixels ----------------------------------------------------


def test_detect_media_picks_the_largest_photo_and_its_caption() -> None:
    msg = _photo_message(caption="the new rack")["message"]
    intent = detect_media(msg)
    assert intent.kind == "image"
    assert intent.file_id == "fullres"
    assert intent.caption == "the new rack"
    assert not intent.is_speech


def test_the_telegram_surface_declares_image_capabilities() -> None:
    from otto.surface.bindings.telegram import TELEGRAM_CAPABILITIES
    from otto.surface.envelope import Capability

    assert Capability.IMAGE_IN in TELEGRAM_CAPABILITIES


def test_a_captioned_photo_without_a_seam_is_still_nothing_to_do() -> None:
    """The surface binding is text-only by contract (it reads ``message.text``
    and nothing else), so before step 4 a photo — even one whose sender wrote
    a caption — had no words the door acted on. A door without the image seam
    keeps exactly that behaviour; caption handling lives in the media seam, not
    the binding."""
    publisher = _Publisher()
    gateway = EventGateway(
        store=_store(),
        secrets=_Secrets(),
        publisher=publisher,
        obs=_Obs(),
    )
    result = gateway.handle(TELEGRAM, _headers(), _photo_body(caption="look at this"))

    assert result.status == NOTHING_TO_DO
    assert publisher.envelopes == []


def test_a_captionless_photo_with_no_seam_is_nothing_to_do() -> None:
    """Before step 4 a bare photo had no words of its own and was gated.
    A text-only door without the image seam keeps exactly that behaviour."""
    publisher = _Publisher()
    gateway = EventGateway(
        store=_store(),
        secrets=_Secrets(),
        publisher=publisher,
        obs=_Obs(),
    )
    result = gateway.handle(TELEGRAM, _headers(), _photo_body())

    assert result.status == NOTHING_TO_DO
    assert publisher.envelopes == []


def test_a_captionless_photo_becomes_a_described_task() -> None:
    calls: list = []
    publisher = _Publisher()
    gateway = EventGateway(
        store=_store(),
        secrets=_Secrets(),
        publisher=publisher,
        obs=_Obs(),
        media=_photon_seam("a dark data centre corridor", calls=calls),
    )
    result = gateway.handle(TELEGRAM, _headers(), _photo_body())

    assert result.status == ACCEPTED, result.reason
    env = publisher.envelopes[0]
    assert env.input == "[image] a dark data centre corridor"
    # Speaking back is only for speech; a photo never asks for an audio reply.
    assert env.wants_voice_reply is False
    # The describer was asked the default question (no caption to lean on).
    _fetched_path, question = calls[0]
    assert question == "describe this"


def test_a_captioned_photo_leads_the_describer_with_the_caption() -> None:
    calls: list = []
    publisher = _Publisher()
    gateway = EventGateway(
        store=_store(),
        secrets=_Secrets(),
        publisher=publisher,
        obs=_Obs(),
        media=_photon_seam("two cabinets, one rack", calls=calls),
    )
    result = gateway.handle(TELEGRAM, _headers(), _photo_body(caption="the new rack"))

    assert result.status == ACCEPTED, result.reason
    env = publisher.envelopes[0]
    # The caption's own words lead, then the model's reading follows, so
    # neither the sender's intent nor the seam's observation is lost.
    assert env.input == "[image] the new rack\ntwo cabinets, one rack"
    assert env.wants_voice_reply is False
    _fetched_path, question = calls[0]
    assert question == "the new rack"


def test_format_image_omits_the_caption_line_when_there_is_none() -> None:
    assert format_image(None, "one rack") == "[image] one rack"
    assert format_image("the rack", "one rack") == "[image] the rack\none rack"
