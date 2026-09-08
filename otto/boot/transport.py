"""Outbound calls to the Telegram Bot API — the transport surface bindings
name but never touch themselves (``otto/surface/bindings/telegram.py``
is a pure function by design; something has to hold the token and the
socket, and this is it).

Same pattern as ``otto.router.providers.LiteLLMClient``: the estate
already talks to an external HTTP API from this repository with stdlib
``urllib.request`` alone, so this module follows it rather than adding a
Telegram client library — the two calls this boot lane needs
(``sendMessage``, ``setWebhook``) are a POST of a JSON body and reading a
JSON response, which is exactly what ``urllib.request`` already does.

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
    def send_message(self, chat_id: int, text: str) -> None: ...

    def set_webhook(self, url: str) -> None: ...


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

    def send_message(self, chat_id: int, text: str) -> None:
        self._post("sendMessage", {"chat_id": chat_id, "text": text})

    def set_webhook(self, url: str) -> None:
        self._post("setWebhook", {"url": url})
