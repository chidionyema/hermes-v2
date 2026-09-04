"""The one fake ``TelegramTransport`` every test in this package uses.

Never a real socket, never ``TelegramHTTPTransport``: a test asserts on
what this recorder saw, not on any HTTP call.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class FakeTransport:
    sent: list[tuple[int, str]] = field(default_factory=list)
    webhooks_set: list[str] = field(default_factory=list)

    def send_message(self, chat_id: int, text: str) -> None:
        self.sent.append((chat_id, text))

    def set_webhook(self, url: str) -> None:
        self.webhooks_set.append(url)


@dataclass
class FakeProviderClient:
    """A model that answers, without egress. The boot pipeline takes a
    ``ProviderClient`` so a test never reaches the estate router."""

    answer: str = "an answer"
    tokens: int = 7

    def complete(self, model: str, payload: str, timeout_seconds: float):
        from otto.router.providers import ProviderResult

        return ProviderResult(
            text=json.dumps(
                {
                    "answer": self.answer,
                    "claims": [
                        {
                            "text": self.answer,
                            "evidence_refs": [],
                            "confidence": "med",
                        }
                    ],
                    "proposed_actions": [],
                    "unknowns": [],
                }
            ),
            tokens=self.tokens,
        )
