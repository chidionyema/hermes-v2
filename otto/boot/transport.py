"""Outbound calls to the Telegram Bot API — the transport surface bindings
name but never touch themselves (``otto/surface/bindings/telegram.py``
is a pure function by design; something has to hold the token and the
socket, and this is it).

Same pattern as ``otto.router.providers.LiteLLMClient``: the estate
already talks to an external HTTP API from this repository with stdlib
``urllib.request`` alone, so this module follows it rather than adding a
Telegram client library — the two calls this boot lane needs
(``sendMessage``, ``setWebhook``, ``sendChatAction``) are a POST of a JSON
body and reading a JSON response, which is exactly what ``urllib.request``
already does.

``TelegramTransport`` is a ``Protocol`` so the test suite injects a
recording fake and never opens a socket; ``TelegramHTTPTransport`` is the
one real implementation, constructed with the token already resolved
(``otto.boot.config.read_token``) — this module never reads the
environment itself, so a call site cannot forget to check for a missing
token before minting the client.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol


class TelegramAPIError(Exception):
    """The Telegram Bot API answered with ``ok: false`` or a non-2xx status."""


class TelegramTransport(Protocol):
    def send_message(self, chat_id: int, text: str) -> int: ...

    def set_webhook(self, url: str) -> None: ...

    def edit_message(self, chat_id: int, message_id: int, text: str) -> None: ...

    def send_chat_action(self, chat_id: int, action: str = "typing") -> None: ...

    def send_voice(
        self, chat_id: int, audio: bytes, filename: str = "reply.ogg"
    ) -> None: ...


@dataclass(frozen=True)
class TelegramHTTPTransport:
    """The one real transport. ``token`` is held only in this instance's
    field and is interpolated into the request URL at call time; it is
    never logged and never included in a raised exception's message."""

    token: str
    api_base: str = "https://api.telegram.org"
    timeout_seconds: float = 10.0

    def _post(self, method: str, payload: dict) -> dict:
        url = f"{self.api_base}/bot{self.token}/{method}"
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(  # noqa: S310 - https only, Telegram Bot API
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:  # noqa: S310
                obj = json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as exc:
            # The body of a Telegram error response is not the token; it is
            # safe to read and surface (it says things like "chat not found").
            detail = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            raise TelegramAPIError(
                f"{method}: HTTP {exc.code} {detail}".strip()
            ) from exc
        except urllib.error.URLError as exc:
            raise TelegramAPIError(f"{method}: {exc.reason}") from exc
        if not obj.get("ok"):
            raise TelegramAPIError(
                f"{method}: {obj.get('description', 'no description')}"
            )
        return obj

    def send_message(self, chat_id: int, text: str) -> int:
        """Send a message and return the ``message_id`` Telegram assigned it.

        Editing a message requires naming it with its own id (crew#892 CP2:
        Otto sends one placeholder and edits it up as the turn proceeds, so
        the founder watches the actual steps). ``sendMessage``'s reply
        carries ``result.message_id``; that id is what ``edit_message``
        posts back, so it is returned here instead of being discarded.
        """
        reply = self._post("sendMessage", {"chat_id": chat_id, "text": text})
        result = reply.get("result") or {}
        message_id = result.get("message_id")
        if not isinstance(message_id, int):
            # A send that Telegram accepted but answered without an id cannot
            # be edited later; callers treat the missing id as "no in-place
            # edit available" rather than crashing the answer.
            return -1
        return message_id

    def set_webhook(self, url: str) -> None:
        self._post("setWebhook", {"url": url})

    def edit_message(self, chat_id: int, message_id: int, text: str) -> None:
        """Replace the text of a message this bot already sent.

        Telegram's ``editMessageText`` names the message by ``chat_id`` and
        ``message_id``. An edit is a courtesy on top of an answer that is
        already guaranteed to have been sent once, so failures raise
        ``TelegramAPIError`` like every other call here; the caller decides
        whether a lost interim edit is worth the sender's answer.
        """
        self._post(
            "editMessageText",
            {"chat_id": chat_id, "message_id": message_id, "text": text},
        )

    def send_chat_action(self, chat_id: int, action: str = "typing") -> None:
        """Telegram's own "the other side is composing" indicator.

        A reasoning lane takes tens of seconds to answer -- `moonshot/kimi-k3`
        answered a three-word question in 30.5s when the estate router was
        probed on 2026-09-04 -- and Telegram shows nothing at all while a
        webhook is being processed, so silence is indistinguishable from the
        bot being down. Telegram clears the indicator after roughly five
        seconds, which is why ``otto.boot.presence`` re-sends it on a timer
        rather than calling this once.

        A failure here is swallowed by the caller, never raised: the
        indicator is a courtesy and must not be able to cost the sender
        their answer."""
        self._post("sendChatAction", {"chat_id": chat_id, "action": action})

    def send_voice(
        self, chat_id: int, audio: bytes, filename: str = "reply.ogg"
    ) -> None:
        """Send a voice message (ADR 0022: spoked replies travel as a
        Telegram voice note). Telegram's ``sendVoice`` takes the audio as
        a multipart upload, so this posts ``multipart/form-data`` rather
        than the JSON the other methods use.

        ``audio`` is the already-synthesised OGG/Opus bytes; the caller
        (the plugin, via the fork's tts) produces them — this method only
        carries bytes to the Bot API and never synthesises.
        """
        import uuid

        boundary = "----otto" + uuid.uuid4().hex
        fields = [
            f'--{boundary}\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n{chat_id}\r\n'.encode(),
            (
                f"--{boundary}\r\nContent-Disposition: form-data; "
                f'name="voice"; filename="{filename}"\r\n'
                f"Content-Type: audio/ogg\r\n\r\n"
            ).encode(),
            audio,
            f"\r\n--{boundary}--\r\n".encode(),
        ]
        body = b"".join(fields)
        url = f"{self.api_base}/bot{self.token}/sendVoice"
        req = urllib.request.Request(  # noqa: S310 - https, Telegram Bot API
            url,
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:  # noqa: S310
                obj = json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as exc:  # noqa: PERF203
            raise TelegramAPIError(f"sendVoice: HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise TelegramAPIError(f"sendVoice: {exc.reason}") from exc
        if not obj.get("ok"):
            raise TelegramAPIError(
                f"sendVoice: {obj.get('description', 'no description')}"
            )
