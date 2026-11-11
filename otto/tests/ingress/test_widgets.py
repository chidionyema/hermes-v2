"""Native message widgets: founder-authenticated briefs, answered in 15s.

A ``widget_query`` update is not a message. It must be answered
synchronously (``answerWidgetQuery`` within 15s), against the founder
allowlist on the channel's row, and never pushed through the async
answering lane. Everything here is offline: the evidence seam is a stub
and the plugin records the answer call without opening a socket.
"""

from __future__ import annotations

import json

from otto.ingress.gateway import FORBIDDEN, NOTHING_TO_DO, UNAVAILABLE, EventGateway
from otto.ingress.plugins import TELEGRAM, OutboundNotSupported
from otto.ingress.store import ChannelBinding, SqliteChannelBindingStore
from otto.spine.envelope import TaskSource
from otto.ingress.widgets import (
    detect_widget_query,
    founder_authorized,
    is_brief_query,
)

SECRET_REF = "vault://otto/acme/telegram"  # noqa: S105 - a reference, not a value
OUTBOUND_REF = "vault://otto/acme/telegram-outbound"  # noqa: S105 - a reference
WEBHOOK_SECRET = "inbound-widget-secret"  # noqa: S105 - test fixture
OUTBOUND_TOKEN = "bot-token-outbound"  # noqa: S105 - test fixture
TENANT = "acme"
FOUNDER_CHAT_ID = 9776
OTHER_CHAT_ID = 9777


def _widget_query_body(
    *, from_id: int, chat_id: int, data: str = "brief", qid: str = "AQr-42"
) -> bytes:
    return json.dumps(
        {
            "widget_query": {
                "id": qid,
                "data": data,
                "from": {"id": from_id, "first_name": "Founder"},
                "chat": {"id": chat_id, "type": "private"},
                "view": {"type": "private", "chat": {"id": chat_id}},
            }
        }
    ).encode("utf-8")


def _headers() -> dict[str, str]:
    return {"X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET}


class _RecordingPlugin:
    """A duck-typed Telegram plugin that records the widget answer rather
    than opening a socket. Implements the slice of ``ChannelPlugin`` the
    gateway touches before the widget path, so the full pipeline runs."""

    channel = TELEGRAM
    task_source = TaskSource.telegram

    def __init__(self) -> None:
        self.answered: list[tuple[str, str, dict]] = []

    @staticmethod
    def header_name() -> str:
        return "X-Telegram-Bot-Api-Secret-Token"

    def present_credential(self, headers, raw_body):
        for key, value in headers.items():
            if key.lower() == "x-telegram-bot-api-secret-token":
                return value
        return None

    def verify(self, presented, secret):
        return presented == secret

    def binding(self, principal_allowlist=None):
        from otto.surface.bindings.telegram import TelegramBinding

        chat_ids = {}
        for address, principal in (principal_allowlist or {}).items():
            try:
                chat_ids[int(address)] = principal
            except (TypeError, ValueError):
                continue
        return TelegramBinding(chat_id_allowlist=chat_ids)

    def send_reply(self, secret, reply_to, text):
        return None

    def answer_widget_query(self, secret, query_id, chat_data):
        self.answered.append((secret, query_id, chat_data))


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
        if secret_ref == OUTBOUND_REF:
            return OUTBOUND_TOKEN
        from otto.ingress.secrets import SecretNotFound

        raise SecretNotFound(secret_ref)


def _store(*, founder: bool = True, outbound: bool = True) -> SqliteChannelBindingStore:
    store = SqliteChannelBindingStore()
    allowlist = {str(FOUNDER_CHAT_ID): "founder"} if founder else {}
    store.register(
        ChannelBinding(
            tenant_id=TENANT,
            channel=TELEGRAM,
            external_id="acme-widget-bot",
            secret_ref=SECRET_REF,
            outbound_secret_ref=OUTBOUND_REF if outbound else None,
            principal_allowlist=allowlist,
        ),
        credential=WEBHOOK_SECRET,
    )
    return store


def _gateway(plugin: _RecordingPlugin, *, founder: bool = True) -> EventGateway:
    return EventGateway(
        store=_store(founder=founder),
        secrets=_Secrets(),
        publisher=_Publisher(),
        obs=_Obs(),
        plugins={TELEGRAM: plugin},
    )


# -- detection ------------------------------------------------------------


def test_detect_widget_query_reads_only_well_typed_widget_updates() -> None:
    body = json.loads(_widget_query_body(from_id=FOUNDER_CHAT_ID, chat_id=FOUNDER_CHAT_ID))
    query = detect_widget_query(body)
    assert query is not None
    assert query.id == "AQr-42"
    assert query.from_id == FOUNDER_CHAT_ID
    assert query.chat_id == FOUNDER_CHAT_ID
    assert query.data == "brief"


def test_detect_widget_query_ignores_plain_messages() -> None:
    assert detect_widget_query({"message": {"text": "hello"}}) is None
    assert detect_widget_query({}) is None


def test_detect_widget_query_ignores_malformed_widget_objects() -> None:
    # Missing id, missing from id, missing chat id: each is None, never a
    # half-built WidgetQuery the caller then half-authenticates.
    q = {"data": "brief", "from": {"id": 9776}, "chat": {"id": 9776}}
    assert detect_widget_query({"widget_query": q}) is None
    q = {"id": "x", "data": "brief", "from": {}, "chat": {"id": 9776}}
    assert detect_widget_query({"widget_query": q}) is None
    q = {"id": "x", "data": "brief", "from": {"id": 9776}, "chat": {}}
    assert detect_widget_query({"widget_query": q}) is None


# -- auth -----------------------------------------------------------------


def test_founder_authorized_requires_both_ids_in_the_allowlist() -> None:
    allowlist = {str(FOUNDER_CHAT_ID): "founder"}
    from otto.ingress.widgets import WidgetQuery

    wq = WidgetQuery(id="x", from_id=FOUNDER_CHAT_ID, chat_id=FOUNDER_CHAT_ID, data="brief")
    assert founder_authorized(wq, allowlist)
    # Sender and view disagree: refuse.
    assert not founder_authorized(
        WidgetQuery(id="x", from_id=OTHER_CHAT_ID, chat_id=FOUNDER_CHAT_ID, data="brief"),
        allowlist,
    )
    # Unknown sender: refuse.
    assert not founder_authorized(
        WidgetQuery(id="x", from_id=OTHER_CHAT_ID, chat_id=OTHER_CHAT_ID, data="brief"),
        allowlist,
    )
    # Empty allowlist: refuse.
    assert not founder_authorized(wq, {})


def test_is_brief_query_matches_only_the_exact_literal() -> None:
    from otto.ingress.widgets import WidgetQuery

    assert is_brief_query(WidgetQuery("x", FOUNDER_CHAT_ID, FOUNDER_CHAT_ID, "brief"))
    assert not is_brief_query(WidgetQuery("x", FOUNDER_CHAT_ID, FOUNDER_CHAT_ID, "BRIEF"))
    assert not is_brief_query(WidgetQuery("x", FOUNDER_CHAT_ID, FOUNDER_CHAT_ID, "other"))


# -- gateway pipeline -----------------------------------------------------


def test_founder_brief_tap_is_answered_synchronously() -> None:
    plugin = _RecordingPlugin()
    gateway = _gateway(plugin)
    result = gateway.handle(
        TELEGRAM,
        _headers(),
        _widget_query_body(from_id=FOUNDER_CHAT_ID, chat_id=FOUNDER_CHAT_ID),
    )

    assert result.status == NOTHING_TO_DO  # nothing to queue; answered in-band
    assert len(plugin.answered) == 1
    secret, qid, chat_data = plugin.answered[0]
    assert secret == OUTBOUND_TOKEN
    assert qid == "AQr-42"
    # Fail-closed brief: no evidence seam wired, so zero and a headline.
    assert chat_data["delivered"] is None
    assert "headline" in chat_data


def test_non_brief_tap_is_ignored_and_not_answered() -> None:
    plugin = _RecordingPlugin()
    gateway = _gateway(plugin)
    result = gateway.handle(
        TELEGRAM,
        _headers(),
        _widget_query_body(from_id=FOUNDER_CHAT_ID, chat_id=FOUNDER_CHAT_ID, data="other"),
    )
    assert result.status == NOTHING_TO_DO
    assert plugin.answered == []


def test_non_founder_sender_is_refused_with_no_answer() -> None:
    plugin = _RecordingPlugin()
    gateway = _gateway(plugin)
    result = gateway.handle(
        TELEGRAM,
        _headers(),
        _widget_query_body(from_id=OTHER_CHAT_ID, chat_id=OTHER_CHAT_ID),
    )
    assert result.status == FORBIDDEN
    assert plugin.answered == []


def test_founder_with_no_allowlist_entry_is_refused() -> None:
    plugin = _RecordingPlugin()
    gateway = _gateway(plugin, founder=False)
    result = gateway.handle(
        TELEGRAM,
        _headers(),
        _widget_query_body(from_id=FOUNDER_CHAT_ID, chat_id=FOUNDER_CHAT_ID),
    )
    assert result.status == FORBIDDEN
    assert plugin.answered == []


def test_no_outbound_credential_resolves_unavailable() -> None:
    plugin = _RecordingPlugin()
    store = _store()
    gateway = EventGateway(
        store=store,
        secrets=_Secrets(),
        publisher=_Publisher(),
        obs=_Obs(),
        plugins={TELEGRAM: plugin},
    )
    # Re-register without an outbound credential.
    store.register(
        ChannelBinding(
            tenant_id=TENANT,
            channel=TELEGRAM,
            external_id="acme-widget-bot",
            secret_ref=SECRET_REF,
            outbound_secret_ref=None,
            principal_allowlist={str(FOUNDER_CHAT_ID): "founder"},
        ),
        credential=WEBHOOK_SECRET,
    )
    result = gateway.handle(
        TELEGRAM,
        _headers(),
        _widget_query_body(from_id=FOUNDER_CHAT_ID, chat_id=FOUNDER_CHAT_ID),
    )
    assert result.status == UNAVAILABLE
    assert plugin.answered == []


def _office_body(*, sender=FOUNDER_CHAT_ID, chat=FOUNDER_CHAT_ID,
                 chat_type="private", text="/office") -> bytes:
    return json.dumps({"message": {
        "message_id": 12, "text": text, "from": {"id": sender},
        "chat": {"id": chat, "type": chat_type}
    }}).encode("utf-8")


def test_office_command_uses_real_telegram_send_not_the_model_queue():
    plugin = _RecordingPlugin()
    sent = []
    def send(secret, chat_id, data, widget_id, url=None):
        sent.append((secret, chat_id, data, widget_id, url))
        return 713
    plugin.send_widget = send
    publisher = _Publisher()
    gateway = EventGateway(store=_store(), secrets=_Secrets(),
                           publisher=publisher, obs=_Obs(), plugins={TELEGRAM: plugin})
    result = gateway.handle(TELEGRAM, _headers(), _office_body())
    assert result.status == 200
    assert result.reason == "office widget delivered"
    assert result.payload["message_id"] == 713
    assert not publisher.envelopes, "/office must NOT become an LLM instruction"
    assert len(sent) == 1
    token, chat_id, data, widget_id, url = sent[0]
    assert token == OUTBOUND_TOKEN
    assert chat_id == str(FOUNDER_CHAT_ID)
    assert widget_id == "otto_founder_compass"
    assert url is None
    assert data["delivered"] is None, "no evidence must be UNKNOWN, not zero"


def test_office_command_denies_unauthorized_group_and_forged_sender():
    plugin = _RecordingPlugin()
    sent = []
    plugin.send_widget = lambda *args, **kwargs: sent.append(args)
    gateway = _gateway(plugin)
    for body in (
        _office_body(sender=OTHER_CHAT_ID),
        _office_body(chat=OTHER_CHAT_ID),
        _office_body(chat_type="supergroup"),
        _office_body(sender=FOUNDER_CHAT_ID, chat_type="group"),
    ):
        result = gateway.handle(TELEGRAM, _headers(), body)
        assert result.status == FORBIDDEN
    assert sent == []


def test_office_command_fails_without_confirmed_telegram_message_id():
    plugin = _RecordingPlugin()
    plugin.send_widget = lambda *args, **kwargs: None
    result = _gateway(plugin).handle(TELEGRAM, _headers(), _office_body())
    assert result.status == UNAVAILABLE


def test_unrelated_telegram_command_does_not_send_founder_widget():
    plugin = _RecordingPlugin()
    called = []
    plugin.send_widget = lambda *args, **kwargs: called.append(1)
    result = _gateway(plugin).handle(
        TELEGRAM, _headers(), _office_body(text="/unrelated")
    )
    assert called == []
    assert result.status in (NOTHING_TO_DO, 202)
