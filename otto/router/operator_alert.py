"""Sends operator alerts when the router cannot serve a task.

The ``Notifier`` protocol is fulfilled here with stdlib only (``urllib.request``).
A failed send is logged but never raised — a notifier that crashes the pipeline
is worse than one that misses a message.

The outbound channel is configured entirely through environment variables so
the compute lane contains no channel-specific logic. Which chat platform
receives the alert is the deployment's concern; this module only knows the
OpenAI-compatible send-message shape.

Configuration (both required; fall back to ``InMemoryNotifier`` when either is absent):
    OTTO_OPERATOR_ALERT_TOKEN   — the bot token used to send the alert.
    OTTO_OPERATOR_CHAT_ID       — the operator's personal chat id (integer).
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

_LOG = logging.getLogger(__name__)

_SEND_PATH = "https://api.telegram.org/bot{token}/sendMessage"
_TIMEOUT = 15.0


@dataclass(frozen=True)
class OperatorAlert:
    """Delivers a NEEDS_HUMAN or QUEUED_BUDGET alert to the operator.

    Channel details are in the environment, not in this class — the
    compute lane remains channel-blind (test: test_compute_is_channel_blind).
    """

    token: str
    chat_id: str | int

    def notify(self, message: str) -> None:
        url = _SEND_PATH.format(token=self.token)
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
            with urllib.request.urlopen(req, timeout=_TIMEOUT):
                pass
        except (urllib.error.URLError, OSError) as exc:
            _LOG.warning("operator alert failed: %s", exc)

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> "OperatorAlert | None":
        """Return an alert sender if both required env vars are set, else None."""
        env = environ if environ is not None else os.environ
        token = env.get("OTTO_OPERATOR_ALERT_TOKEN", "") or env.get(
            "OTTO_TELEGRAM_BOT_TOKEN", ""
        )
        chat_id = env.get("OTTO_OPERATOR_CHAT_ID", "")
        if not token or not chat_id:
            return None
        return cls(token=token, chat_id=chat_id)
