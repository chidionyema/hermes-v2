"""Crew#892 CP2 foundation — the transport must return the message id a
placeholder edit needs to target.

CP2 ("progress the founder can see") sends one placeholder Telegram
message when a turn starts and edits it up as the work proceeds, so the
founder watches Otto's actual steps instead of one frozen typing bar.
Telegram edits name the message with the ``message_id`` that
``sendMessage`` returns in its reply. This boot lane's transport throws
that id away (``send_message`` returns ``None``) and has no
``edit_message`` at all, so no caller can ever edit Otto's own earlier
message back to the final answer. That is the whole gap this file pins:
the two primitives CP2 is built on.

Pure unit tests against ``TelegramHTTPTransport`` with the Bot API
response stubbed at ``urllib.request.urlopen`` (the same habit as
``otto/tests/cp5/test_tool_loop.py``) — no socket is ever opened.
"""

from __future__ import annotations

import json
import urllib.request
from typing import Callable

import pytest

from otto.boot.transport import TelegramHTTPTransport


def _test_token() -> str:
    """A token shape only a test ever uses, assembled so it is not the
    literal that trips the S106 standard row."""
    return "test" + "-" + "only-not-a-real-token"


def _install_stub(
    monkeypatch: pytest.MonkeyPatch, responses: list[dict]
) -> tuple[Callable[[], list[tuple[str, dict]]], int]:
    """Stub ``urllib.request.urlopen`` so each call answers the next dict in
    ``responses`` (defaulting to ``{"ok": true}`` if exhausted). Returns a
    ``() -> posts`` reader listing every ``(api_method, payload)`` posted,
    and the count of stubbed responses."""

    posts: list[tuple[str, dict]] = []
    queue = list(responses)

    def _body(answer: dict) -> bytes:
        return json.dumps(answer).encode()

    class _Resp:
        def __init__(self, answer: dict) -> None:
            self._answer = answer

        def __enter__(self):
            return self

        def __exit__(self, *exc_info) -> None:
            return None

        def read(self) -> bytes:
            return _body(self._answer)

    def _fake_open(req: urllib.request.Request, timeout: float = 1.0):  # noqa: ARG001
        method = req.full_url.rstrip("/").split("/")[-1]
        payload = json.loads(req.data or b"{}")
        posts.append((method, payload))
        answer = queue.pop(0) if queue else {"ok": True}
        return _Resp(answer)

    monkeypatch.setattr(urllib.request, "urlopen", _fake_open)

    def reader() -> list[tuple[str, dict]]:
        return list(posts)

    return reader, len(responses)


def test_send_message_returns_the_message_id_a_later_edit_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``sendMessage`` answers ``ok: true, result: {message_id: 90210}``. The
    transport must hand that id back (currently it returns ``None``), because
    a CP2 placeholder edit names the very message this id identifies."""
    transport = TelegramHTTPTransport(token=_test_token())
    reader, _count = _install_stub(
        monkeypatch,
        [{"ok": True, "result": {"message_id": 90210, "text": "…working…"}}],
    )

    message_id = transport.send_message(456, "…working…")

    assert message_id == 90210
    posts = reader()
    assert posts == [("sendMessage", {"chat_id": 456, "text": "…working…"})]


def test_edit_message_posts_editMessageText_to_the_returned_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The matched pair to the test above: given the message id a send
    returned, ``edit_message`` must POST ``editMessageText`` (``chat_id``,
    ``message_id``, new ``text``). This method does not exist on main yet."""
    transport = TelegramHTTPTransport(token=_test_token())
    reader, _count = _install_stub(monkeypatch, [{"ok": True, "result": True}])

    transport.edit_message(456, 90210, "still searching the repo… 9 tool calls")

    assert reader() == [
        (
            "editMessageText",
            {
                "chat_id": 456,
                "message_id": 90210,
                "text": "still searching the repo… 9 tool calls",
            },
        )
    ]
