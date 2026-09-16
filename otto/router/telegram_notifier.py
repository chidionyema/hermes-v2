"""Operator notifier that delivers NEEDS_HUMAN and QUEUED_BUDGET alerts via Telegram.

The ``Notifier`` protocol is fulfilled here with stdlib only (``urllib.request``).
A failed send is logged but never raised — a notifier that crashes the pipeline
is worse than one that misses a message.

Configuration (both required; fall back to ``InMemoryNotifier`` when either is absent):
    OTTO_TELEGRAM_BOT_TOKEN — the bot that sends the alert.
    OTTO_OPERATOR_CHAT_ID   — the operator's personal chat id (integer).
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_SEND_MESSAGE_PATH = "https://api.telegram.org/bot{token}/sendMessage"
_CONNECT_TIMEOUT = 5.0
_READ_TIMEOUT = 10.0


@dataclass(frozen=True)
class TelegramNotifier:
    """Sends operator alerts through the Telegram Bot API."""

    token: str
    chat_id: str | int

    def notify(self, message: str) -> None:
        url = _SEND_MESSAGE_PATH.format(token=self.token)
        payload = json.dumps(
            {"chat_id": self.chat_id, "text": message, "parse_mode": "HTML"}
        ).encode()
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                req, timeout=(_CONNECT_TIMEOUT + _READ_TIMEOUT)
            ):
                pass
        except (urllib.error.URLError, OSError) as exc:
            logger.warning("operator notify failed: %s", exc)

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> TelegramNotifier | None:
        """Return a notifier if both required env vars are set, else None."""
        env = environ if environ is not None else os.environ
        token = env.get("OTTO_TELEGRAM_BOT_TOKEN", "")
        chat_id = env.get("OTTO_OPERATOR_CHAT_ID", "")
        if not token or not chat_id:
            return None
        return cls(token=token, chat_id=chat_id)
