"""The one fake ``TelegramTransport`` every test in this package uses.

Never a real socket, never ``TelegramHTTPTransport``: a test asserts on
what this recorder saw, not on any HTTP call.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FakeTransport:
    sent: list[tuple[int, str]] = field(default_factory=list)
    webhooks_set: list[str] = field(default_factory=list)

    def send_message(self, chat_id: int, text: str) -> None:
        self.sent.append((chat_id, text))

    def set_webhook(self, url: str) -> None:
        self.webhooks_set.append(url)
