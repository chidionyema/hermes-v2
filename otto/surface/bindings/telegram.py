"""The Telegram surface binding (spec bullet 5): the launch surface.

Pure function — no network call, no bot token anywhere in this module.
The principal is resolved from a chat-id allow-list the caller passes in
at construction time (LAW 46: no checkout, host, port, account or
credential is ever a literal in a file) — this module names no chat id
of its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from otto.surface.adapter import RenderedMessage
from otto.surface.envelope import Capability, SurfaceEnvelope, TrustClass
from otto.surface.identity import BOUND_ACCOUNT, validate_principal_source
from otto.surface.renderer import parts_from_response, render_parts

TELEGRAM_CAPABILITIES: frozenset[Capability] = frozenset(
    {
        Capability.TEXT,
        Capability.RICH,
        Capability.IMAGE_IN,
        Capability.IMAGE_OUT,
        Capability.APPROVAL_GESTURE,
    }
)


@dataclass(frozen=True, slots=True)
class TelegramBinding:
    """``chat_id_allowlist`` maps a Telegram chat id to a bound-account
    principal name. Supplied by the caller (config, not this file) — a
    fresh, empty binding trusts no chat id and normalizes every update to
    ``principal=None`` / ``trust_class=UNTRUSTED``.
    """

    chat_id_allowlist: dict[int, str]

    def normalize(self, native_event: dict[str, Any]) -> SurfaceEnvelope:
        message = native_event.get("message", native_event)
        chat = message.get("chat", {})
        chat_id = chat.get("id")
        text = message.get("text", "")

        principal = self.chat_id_allowlist.get(chat_id) if chat_id is not None else None
        if principal is not None:
            validate_principal_source(BOUND_ACCOUNT)
            trust_class = TrustClass.OPERATOR
        else:
            trust_class = TrustClass.UNTRUSTED

        return SurfaceEnvelope(
            surface="telegram",
            principal=principal,
            trust_class=trust_class,
            capabilities=TELEGRAM_CAPABILITIES,
            content=text,
            received_at=_parse_date(message.get("date")),
        )

    def render(
        self, response: dict[str, Any], capabilities: frozenset[Capability]
    ) -> RenderedMessage:
        return render_parts(
            parts_from_response(response), capabilities, surface="telegram"
        )


def _parse_date(raw: int | None) -> datetime:
    if raw is None:
        return datetime.now(timezone.utc)
    return datetime.fromtimestamp(raw, tz=timezone.utc)
