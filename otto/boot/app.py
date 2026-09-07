"""The webhook request handled as a pure function, decoupled from any
socket. ``otto.boot.server`` wraps ``handle_webhook_body`` with stdlib
``http.server``; the test suite calls it directly with bytes it built
itself, so no test in this package ever opens a real socket.

Failure contract for a webhook body (the four cases named in the task):

* not valid JSON at all -> 400, dropped, no crash
* valid JSON but not a JSON object -> 400, dropped, no crash
* a JSON object whose ``message``/``chat`` fields are not the shapes
  Telegram sends (a string where an object belongs) -> 400, dropped,
  no crash — this is the guard that keeps
  ``otto.surface.bindings.telegram.TelegramBinding.normalize`` (which
  assumes a dict-shaped ``message``/``chat`` by design, per CP2b) from
  ever seeing a shape it was not built to handle
* a well-formed Telegram update this deployment has no reply for
  (an unrecognised chat id, an empty text, a non-text update) -> 200,
  dropped, no crash — Telegram's own guidance is to ack fast and never
  retry-storm a webhook over content the receiver legitimately ignored
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from otto.boot.pipeline import ObsHandles, deliver, extract_chat_id, process_update
from otto.boot.presence import typing_while
from otto.boot.transport import TelegramTransport
from otto.boot.visible_progress import ProgressEditor
from otto.gateway.core import ToolGateway
from otto.surface.bindings.telegram import TelegramBinding

OK_RESPONSE = b'{"ok":true}'
BAD_REQUEST_RESPONSE = b'{"ok":false,"error":"not a telegram-shaped update"}'
DROPPED_RESPONSE = b'{"ok":true,"delivered":false}'
DELIVERED_RESPONSE = b'{"ok":true,"delivered":true}'


@dataclass(frozen=True)
class WebhookResult:
    status: int
    body: bytes


def _is_telegram_shaped(native_event: object) -> bool:
    """Structural check only — never inspects field values, only that
    the nesting a Telegram update always uses (``message`` an object,
    ``message.chat`` an object, when either key is present at all) holds.
    An update with no ``message`` key at all (a callback query, an
    edited-message-only update, ...) is still telegram-shaped; this
    boot lane simply has nothing to do with it."""
    if not isinstance(native_event, dict):
        return False
    message = native_event.get("message")
    if message is not None and not isinstance(message, dict):
        return False
    if isinstance(message, dict):
        chat = message.get("chat")
        if chat is not None and not isinstance(chat, dict):
            return False
    return True


def handle_webhook_body(
    raw_body: bytes,
    *,
    binding: TelegramBinding,
    gateway: ToolGateway,
    obs: ObsHandles,
    transport: TelegramTransport,
) -> WebhookResult:
    """Parse, process and (maybe) reply to one webhook delivery. Never
    raises: every failure mode named above returns a ``WebhookResult``
    instead."""
    try:
        native_event = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return WebhookResult(400, BAD_REQUEST_RESPONSE)

    if not _is_telegram_shaped(native_event):
        return WebhookResult(400, BAD_REQUEST_RESPONSE)

    try:
        # CP2 (crew#892): an allowlisted sender whose text is non-empty is
        # guaranteed to reach answer_envelope, so give that chat one
        # placeholder before the (possibly half-minute) model pass and pass
        # the editor down so the final answer is edited into that same
        # message rather than a second fresh send. Everyone else gets None
        # and stays on today's byte-identical path.
        chat_id = extract_chat_id(native_event)
        editor = _placeholder_for(
            native_event,
            chat_id=chat_id,
            binding=binding,
            transport=transport,
            obs=obs,
        )
        # The model call inside process_update blocks for as long as the
        # lane takes to think -- half a minute on a reasoning lane. Telegram
        # shows nothing at all meanwhile, so the sender is told the bot is
        # composing rather than left reading silence (founder, 2026-09-04).
        with typing_while(transport, chat_id):
            outcome = process_update(
                native_event,
                binding=binding,
                registry_gateway=gateway,
                obs=obs,
                progress=editor,
            )
        if editor is not None and editor.message_id is not None:
            delivered = deliver(outcome, transport, edit_message_id=editor.message_id)
        else:
            delivered = deliver(outcome, transport)
    except Exception as exc:  # noqa: BLE001 - a webhook must never crash the process
        obs.boot.error("webhook.pipeline_error", _fallback_ctx(), error=str(exc))
        return WebhookResult(200, DROPPED_RESPONSE)

    return WebhookResult(200, DELIVERED_RESPONSE if delivered else DROPPED_RESPONSE)


def _placeholder_for(
    native_event: dict,
    *,
    chat_id: int | None,
    binding: TelegramBinding,
    transport: TelegramTransport,
    obs,  # ObsHandles — see handle_webhook_body
) -> ProgressEditor | None:
    """One placeholder for a turn that will truly be answered.

    Returns an editor only for an update from an allowlisted person (the
    founder lane CP2 exists for) with non-empty text — the two cases
    ``answer_envelope`` is guaranteed to answer. Anything else returns
    ``None`` so the plain, untouched send path runs; CP2 is opt-in per
    message, never global. Sending a placeholder for a message that would
    never earn an answer would strand a "… working …" that no final edit
    ever resolved, so unknown or empty senders stay on today's path
    byte-for-byte.
    """
    if chat_id is None:
        return None
    if binding.chat_id_allowlist.get(chat_id) is None:
        return None
    text = (native_event.get("message") or {}).get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    editor = ProgressEditor(transport)
    editor.begin(chat_id, "… working …", obs=obs)
    return editor


def _fallback_ctx():
    """A context to log against when the pipeline failed before minting
    one of its own — a fresh ULID beats losing the log line."""
    from otto.obs.core import TaskContext

    return TaskContext.new()
