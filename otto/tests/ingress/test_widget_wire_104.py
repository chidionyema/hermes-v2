"""Production Telegram Bot API 10.4 integration contract (real JSON requests).

No mock method return masquerades as a send: these tests intercept the actual
urllib HTTP request and inspect its serialized payload at the transport edge.
The catalogue tests exercise psycopg's default tuple row shape, not monkeypatched
metrics. Run on the existing cloud-hosted Otto suite.
"""
from __future__ import annotations

import json
import urllib.request
import zlib
from pathlib import Path

import pytest

from otto.boot.transport import TelegramAPIError, TelegramHTTPTransport
from otto.ingress.evidence import FounderEvidenceStore, DEFAULT_NO_EVIDENCE
from otto.ingress.gateway import EventGateway

BOT = "123:TEST-ONLY"
CHAT = 424242


class _Response:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return json.dumps(self.value).encode("utf-8")


def test_send_widget_reaches_real_http_request_with_html_and_private_query_contract(monkeypatch):
    requests = []

    def fake_urlopen(request, timeout):
        assert request.full_url.startswith("https://api.telegram.org/bot" + BOT)
        assert request.get_header("Content-type") == "application/json"
        method = request.full_url.rsplit("/", 1)[-1]
        data = json.loads(request.data)
        requests.append((method, data))
        return _Response({"ok": True, "result": {"message_id": 713}})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = TelegramHTTPTransport(token=BOT)
    initial = {"r": 0, "at": 0, "delivered": None, "active": None, "issues": None}
    assert client.send_widget(CHAT, "otto_founder_compass", initial) == 713
    assert [name for name, _ in requests] == ["sendWidget"]
    body = requests[0][1]
    assert body["chat_id"] == CHAT
    assert "TelegramWidget" in body["html"]
    assert len(body["html"].encode("utf-8")) <= 32768
    assert len(zlib.compress(body["html"].encode("utf-8"), level=9)) <= 4096
    assert body["allow_queries"] is True
    assert body["is_shared"] is False
    assert body["caption"]
    assert json.loads(body["chat_data"]) == initial
    assert "widget_id" not in body
    assert "url" not in body


def test_refresh_and_persistent_edit_use_different_telegram_wire_parameters(monkeypatch):
    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request.full_url.rsplit("/", 1)[-1], json.loads(request.data)))
        return _Response({"ok": True, "result": True})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = TelegramHTTPTransport(token=BOT)
    refreshed = {"r": 7, "at": 111, "delivered": 1}
    client.answer_widget_query("telegram-query-1", refreshed)
    client.edit_message_widget_data(CHAT, 713, refreshed)
    assert [name for name, _ in requests] == ["answerWidgetQuery", "editMessageWidgetData"]
    assert requests[0][1] == {"widget_query_id": "telegram-query-1",
                               "data": json.dumps(refreshed, separators=(",", ":"))}
    assert requests[1][1] == {"chat_id": CHAT, "message_id": 713,
                               "chat_data": json.dumps(refreshed, separators=(",", ":"))}
    assert "chat_data" not in requests[0][1]
    assert "data" not in requests[1][1]


def test_invalid_widget_and_oversized_data_never_reach_network(monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("invalid widget must not touch Telegram")
    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    client = TelegramHTTPTransport(token=BOT)
    with pytest.raises(ValueError):
        client.send_widget(CHAT, "attacker_widget", {"r": 0})
    with pytest.raises(ValueError):
        client.send_widget(CHAT, "otto_founder_compass", {"x": "🔥" * 300})
    with pytest.raises(ValueError):
        client.send_widget(CHAT, "otto_founder_compass", {"r": 0}, url="https://attacker.invalid")
    with pytest.raises(ValueError):
        client.answer_widget_query("q", {"huge": "x" * 1100})
    with pytest.raises(ValueError):
        client.edit_message_widget_data(CHAT, -1, {"r": 1})


def test_telegram_rejection_does_not_claim_delivery(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_a, **_k: _Response({"ok": False, "description": "denied"}))
    with pytest.raises(TelegramAPIError):
        TelegramHTTPTransport(token=BOT).send_widget(CHAT, "otto_founder_compass", {"r": 1})


def test_unavailable_ledger_is_unknown_not_a_misleading_zero():
    from otto.ingress.gateway import EventGateway
    assert DEFAULT_NO_EVIDENCE["delivered"] is None
    assert DEFAULT_NO_EVIDENCE["active"] is None
    assert DEFAULT_NO_EVIDENCE["issues"] is None
    # A production handler without an evidence provider must also be fail-closed.
    gateway = object.__new__(EventGateway)
    gateway._widgets = None
    assert gateway._safe_brief()["delivered"] is None


def test_live_psycopg_default_tuple_row_contract(monkeypatch):
    from otto.ingress import evidence

    class Row:
        def fetchone(self):
            # The actual psycopg default row factory yields tuples, not attrs.
            return (3, 2, 1, None, "INS-42", "Proof from ledger")

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, query):
            assert "FROM catalog.instructions" in query
            return Row()

    calls = []
    def fake_connect(dsn, **kwargs):
        calls.append((dsn, kwargs))
        return Conn()
    monkeypatch.setattr(evidence.psycopg, "connect", fake_connect)
    store = FounderEvidenceStore("host=mock dbname=estate")
    assert store._counts() == (3, 2, 1)
    assert store._latest() == ("INS-42", "Proof from ledger")
    brief = store.brief()
    assert brief["delivered"] == 3
    assert brief["id"] == "INS-42"
    assert brief["proof"] == "Proof from ledger"
    assert calls
    assert all(kw["connect_timeout"] == 2 for _, kw in calls)
