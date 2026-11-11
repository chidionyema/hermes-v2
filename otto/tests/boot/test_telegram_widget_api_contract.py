"""Telegram Bot API 10.4 contract, exercised over the actual urllib HTTP transport.

The transport formerly emitted widget_id instead of required html, and
chat_data instead of answerWidgetQuery's required data. These tests
record the exact POST body and reject regressions before any real bot call.
"""
from __future__ import annotations

import io
import json
import zlib
from pathlib import Path
from unittest.mock import patch

import pytest

from otto.boot.transport import TelegramAPIError, TelegramHTTPTransport

FOUNDER = 123456


def capture_bot_api(*, response=None):
    requests = []

    def urlopen(request, timeout):
        assert timeout == 10
        assert request.get_method() == "POST"
        assert request.full_url.startswith("https://api.telegram.org/botLOCAL-TEST/")
        assert request.get_header("Content-type") == "application/json"
        data = json.loads(request.data)
        requests.append((request.full_url.rsplit("/", 1)[-1], data))
        body = response or {"ok": True, "result": {"message_id": 77}}
        return io.BytesIO(json.dumps(body).encode("utf-8"))

    return requests, urlopen


def test_send_widget_uses_inline_html_and_returns_actual_telegram_message_id():
    requests, urlopen = capture_bot_api()
    transport = TelegramHTTPTransport(token="LOCAL-TEST")
    with patch("otto.boot.transport.urllib.request.urlopen", side_effect=urlopen):
        message_id = transport.send_widget(
            FOUNDER, "otto_founder_compass", {"r": 1, "at": 1794391200000}
        )
    assert message_id == 77
    assert len(requests) == 1
    method, data = requests[0]
    assert method == "sendWidget"
    assert set(data) == {
        "chat_id", "html", "chat_data", "caption", "allow_queries", "is_shared"
    }, "Do not send Bot API 10.4 unknown fields widget_id or url"
    assert data["chat_id"] == FOUNDER
    assert data["is_shared"] is False
    assert data["allow_queries"] is True
    assert data["caption"]
    assert "<html" in data["html"]
    raw = data["html"].encode("utf-8")
    assert 1 <= len(raw) <= 32768
    assert len(zlib.compress(raw, 9)) <= 4096
    assert json.loads(data["chat_data"]) == {"r": 1, "at": 1794391200000}


def test_answer_widget_query_posts_data_not_chat_data():
    requests, urlopen = capture_bot_api(response={"ok": True, "result": True})
    transport = TelegramHTTPTransport(token="LOCAL-TEST")
    with patch("otto.boot.transport.urllib.request.urlopen", side_effect=urlopen):
        transport.answer_widget_query("query-42", {"r": 8, "delivered": 1})
    assert requests == [(
        "answerWidgetQuery",
        {"widget_query_id": "query-42", "data": '{"r":8,"delivered":1}'},
    )]


@pytest.mark.parametrize("chat_id,widget_id,url", [
    (FOUNDER, "missing-asset", None),
    (FOUNDER, "otto_founder_compass", "https://example.invalid/widget.html"),
    (0, "otto_founder_compass", None),
    ("founder", "otto_founder_compass", None),
])
def test_no_unsupported_send_is_put_on_the_wire(chat_id, widget_id, url):
    requests, urlopen = capture_bot_api()
    with patch("otto.boot.transport.urllib.request.urlopen", side_effect=urlopen):
        with pytest.raises(ValueError):
            TelegramHTTPTransport(token="LOCAL-TEST").send_widget(
                chat_id, widget_id, {"r": 1}, url=url
            )
    assert not requests


def test_reject_oversized_state_before_any_network_call():
    requests, urlopen = capture_bot_api()
    with patch("otto.boot.transport.urllib.request.urlopen", side_effect=urlopen):
        with pytest.raises(ValueError, match="1024"):
            TelegramHTTPTransport(token="LOCAL-TEST").send_widget(
                FOUNDER, "otto_founder_compass", {"private": "a" * 1025}
            )
        with pytest.raises(ValueError, match="1024"):
            TelegramHTTPTransport(token="LOCAL-TEST").answer_widget_query(
                "query", {"private": "a" * 1025}
            )
    assert requests == []


def test_telegram_200_without_a_message_receipt_is_not_delivery():
    _, urlopen = capture_bot_api(response={"ok": True, "result": True})
    with patch("otto.boot.transport.urllib.request.urlopen", side_effect=urlopen):
        with pytest.raises(TelegramAPIError, match="no message_id"):
            TelegramHTTPTransport(token="LOCAL-TEST").send_widget(
                FOUNDER, "otto_founder_compass", {"r": 1}
            )


def test_body_copies_repository_owned_widget_html_not_a_remote_url():
    asset = Path(__file__).resolve().parents[2] / "assets" / "otto_founder_compass.html"
    requests, urlopen = capture_bot_api()
    with patch("otto.boot.transport.urllib.request.urlopen", side_effect=urlopen):
        TelegramHTTPTransport(token="LOCAL-TEST").send_widget(
            FOUNDER, "otto_founder_compass", {"r": 1}
        )
    assert requests[0][1]["html"].encode("utf-8") == asset.read_bytes()


def test_silent_edit_posts_message_scoped_chat_data_not_new_chat_message():
    requests, urlopen = capture_bot_api(response={"ok": True, "result": True})
    transport = TelegramHTTPTransport(token="LOCAL-TEST")
    with patch("otto.boot.transport.urllib.request.urlopen", side_effect=urlopen):
        transport.edit_message_widget_data(FOUNDER, 77, {"r": 9, "proof": "real"})
    assert requests == [(
        "editMessageWidgetData",
        {
            "chat_id": FOUNDER, "message_id": 77,
            "chat_data": '{"r":9,"proof":"real"}',
        },
    )]


def test_silent_edit_refuses_nonoriginal_message_and_oversized_state():
    requests, urlopen = capture_bot_api()
    with patch("otto.boot.transport.urllib.request.urlopen", side_effect=urlopen):
        for chat_id, message_id in ((0, 1), (FOUNDER, 0), (FOUNDER, True)):
            with pytest.raises(ValueError):
                TelegramHTTPTransport(token="LOCAL-TEST").edit_message_widget_data(
                    chat_id, message_id, {"r": 1}
                )
        with pytest.raises(ValueError, match="1024"):
            TelegramHTTPTransport(token="LOCAL-TEST").edit_message_widget_data(
                FOUNDER, 77, {"private": "a" * 1025}
            )
    assert requests == []
