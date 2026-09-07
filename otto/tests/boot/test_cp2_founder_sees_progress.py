"""crew#892 CP2: the placeholder a founder can watch being edited in place.

These pin the end-to-end contract of ``otto.boot.app.handle_webhook_body``
through a recording ``FakeTransport``:

* an allowlisted founder whose text will be answered gets ONE placeholder
  first, and the final answer is edited into that very message, so the
  transcript reads as one living note, not a fresh send every turn;
* a transport with no room for an in-place edit (a send that answers without
  a ``message_id``) falls back to today's exact single-send shape — CP2 is a
  courtesy, opt-in per message, never a behaviour that replaces the plain
  path;
* an unreachable placeholder never costs the sender their reply.
"""

from __future__ import annotations

import pytest

from otto.boot.app import DELIVERED_RESPONSE, DROPPED_RESPONSE, handle_webhook_body
from otto.boot.pipeline import boot_obs_handles, build_registry
from otto.gateway.core import ToolGateway
from otto.surface.bindings.telegram import TelegramBinding
from otto.tests.boot.fakes import FakeTransport

ALLOWLIST = {111: "founder"}
PLACEHOLDER = "… working …"


@pytest.fixture()
def deps():
    obs = boot_obs_handles()
    try:
        yield {
            "binding": TelegramBinding(chat_id_allowlist=ALLOWLIST),
            "gateway": ToolGateway(registry=build_registry()),
            "obs": obs,
            "transport": FakeTransport(),
        }
    finally:
        for handle in (obs.boot, obs.spine, obs.gateway, obs.router, obs.memory):
            handle.shutdown()


class _NoPlaceholderTransport(FakeTransport):
    """A send that Telegram accepts but answers without a ``message_id`` —
    the transport reports it cannot host an in-place edit."""

    def send_message(self, chat_id: int, text: str) -> int:
        self.sent.append((chat_id, text))
        return -1


class _RefusingTransport(FakeTransport):
    """Telegram refuses the first (placeholder) send but answers later ones;
    a placeholder failure must never cost the founder their reply."""

    def __init__(self) -> None:
        super().__init__()
        self._refused_placeholder = False

    def send_message(self, chat_id: int, text: str) -> int:
        if not self._refused_placeholder and text == PLACEHOLDER:
            self._refused_placeholder = True
            raise RuntimeError("upstream refused the send")
        self.sent.append((chat_id, text))
        mid = self._next_message_id
        self._next_message_id += 1
        self.message_ids[(chat_id, text)] = mid
        return mid


def _founder_body(text: str = "hi otto") -> bytes:
    return (
        b'{"message": {"chat": {"id": 111}, "text": "'
        + text.encode()
        + b'", "date": 1700000000}}'
    )


def test_founder_answer_is_edited_into_the_single_placeholder(deps) -> None:
    """The founder's transcript is one living message: exactly one send (the
    placeholder) and the delivered reply lands as an edit of that self-same
    message — never as a second fresh send."""
    result = handle_webhook_body(_founder_body(), **deps)
    assert result.status == 200
    assert result.body == DELIVERED_RESPONSE
    transport = deps["transport"]
    # One placeholder message and that is the only thing ever *sent*.
    assert transport.sent == [(111, PLACEHOLDER)]
    # ...and the reply was delivered by editing it, in place.
    assert len(transport.edited) >= 1
    edited_chat, edited_message_id, edited_text = transport.edited[-1]
    assert edited_chat == 111
    placeholder_id = transport.message_ids[(111, PLACEHOLDER)]
    assert edited_message_id == placeholder_id
    # The edited text is the real reply, not the placeholder, not the sender's
    # own words echoed back.
    assert edited_text != PLACEHOLDER
    assert "hi otto" not in edited_text
    assert edited_text.strip() != ""


def test_no_message_id_degrades_without_losing_the_answer(deps) -> None:
    """A transport that cannot host an in-place edit (send answers without a
    message id) still delivers the founder's answer as a normal send; nothing
    is edited and nothing is lost. CP2 is a courtesy that must never hold the
    answer hostage to an uneditable transport."""
    deps["transport"] = _NoPlaceholderTransport()
    result = handle_webhook_body(_founder_body(), **deps)
    assert result.status == 200
    assert result.body == DELIVERED_RESPONSE
    transport = deps["transport"]
    # The real answer arrived as a send.
    assert transport.sent[-1][0] == 111
    assert transport.sent[-1][1] != PLACEHOLDER
    assert transport.sent[-1][1].strip() != ""
    assert transport.edited == []


def test_a_refused_placeholder_never_loses_the_answer(deps) -> None:
    """Even when the placeholder is refused by Telegram, the pipeline never
    crashes and the founder still gets their answer as a normal fresh send."""
    deps["transport"] = _RefusingTransport()
    result = handle_webhook_body(_founder_body(), **deps)
    assert result.status == 200
    assert result.body == DELIVERED_RESPONSE
    # The placeholder refusal was swallowed (progress.begin_failed), and the
    # answer still rode a normal fresh send.
    assert sum(1 for _, text in deps["transport"].sent if text != PLACEHOLDER) == 1
    assert deps["transport"].sent[-1][1] != PLACEHOLDER
    assert deps["transport"].edited == []


def test_unknown_sender_gets_no_placeholder_and_no_answer(deps) -> None:
    """An unrecognised chat id must never see a stray placeholder — CP2 is
    only for the founder lane; today's path for strangers is intact."""
    body = b'{"message": {"chat": {"id": 999}, "text": "hi", "date": 1700000000}}'
    result = handle_webhook_body(body, **deps)
    assert result.status == 200
    assert result.body == DROPPED_RESPONSE
    assert deps["transport"].sent == []
    assert deps["transport"].edited == []


def test_blank_message_gets_no_placeholder(deps) -> None:
    """An empty-text message never earns a placeholder (nothing to answer)."""
    body = b'{"message": {"chat": {"id": 111}, "text": "   ", "date": 1700000000}}'
    result = handle_webhook_body(body, **deps)
    assert result.status == 200
    assert result.body == DROPPED_RESPONSE
    assert deps["transport"].sent == []
