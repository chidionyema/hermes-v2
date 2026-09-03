"""Per-channel plugins: the only place a channel's name means anything.

A plugin answers three questions about one channel, and nothing else:

1. *Where does this channel put its credential?* Telegram puts a shared
   secret in the ``X-Telegram-Bot-Api-Secret-Token`` header; a generic
   HTTP caller uses ``Authorization: Bearer``; Slack signs the body.
   Each of those is a few lines here and zero lines anywhere else.
2. *Is the presented credential the right one?* A constant-time compare,
   or for a signing channel, a recomputed signature.
3. *Which surface binding turns this channel's payload into the neutral
   envelope?* The existing ``otto.surface.bindings`` are reused as-is;
   this package adds no parsing of its own.

Everything else — looking the customer up, minting the task, publishing
it, answering the socket — is channel-independent and lives in
``gateway.py``. That is what makes adding Slack a new file here rather
than a change spread across the platform.

Telegram is plugin number one because it is the channel already live in
the cluster. It has no privileged position in the code: it is registered
in the same table as every other plugin, and the gateway cannot tell the
difference.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from otto.spine.envelope import TaskSource
from otto.surface.adapter import SurfaceAdapter
from otto.surface.bindings.http import HttpBinding
from otto.surface.bindings.telegram import TelegramBinding

TELEGRAM = "telegram"
HTTP = "http"


class ChannelPlugin(Protocol):
    """One channel's credential rules and its surface binding."""

    channel: str
    task_source: TaskSource

    def present_credential(
        self, headers: Mapping[str, str], raw_body: bytes
    ) -> str | None:
        """The credential this request presented, or ``None`` when the
        request carried none at all."""
        ...

    def verify(self, presented: str, secret: str) -> bool:
        """Whether the presented credential matches the customer's own
        secret. Constant time — a timing difference here is a way to
        guess another customer's token one byte at a time."""
        ...

    def binding(self) -> SurfaceAdapter:
        """The surface binding that normalises this channel's payload."""
        ...


def _header(headers: Mapping[str, str], name: str) -> str | None:
    """Case-insensitive header read. HTTP header names are
    case-insensitive by specification, and different servers and proxies
    hand them over in different cases, so a plugin must never index a
    plain dict by one exact spelling."""
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value
    return None


@dataclass(frozen=True)
class TelegramPlugin:
    """Plugin one: Telegram's shared secret token.

    Telegram sends the value registered with ``setWebhook`` in the
    ``X-Telegram-Bot-Api-Secret-Token`` header on every delivery. Each
    customer registers a different value, so the header both authenticates
    the delivery and identifies the customer — which is exactly the
    "which client does this token belong to" lookup the gateway performs.
    """

    channel: str = TELEGRAM
    task_source: TaskSource = TaskSource.telegram
    header_name: str = "X-Telegram-Bot-Api-Secret-Token"

    def present_credential(
        self, headers: Mapping[str, str], raw_body: bytes
    ) -> str | None:
        return _header(headers, self.header_name) or None

    def verify(self, presented: str, secret: str) -> bool:
        return hmac.compare_digest(presented, secret)

    def binding(self) -> SurfaceAdapter:
        # An empty allow-list: this gateway authenticates the *channel*,
        # not the individual person on the far side of it, so every
        # sender starts untrusted and the taint cap applies. Per-person
        # trust arrives with the control plane's user mapping; promoting
        # a sender to operator on the strength of a channel-level secret
        # would hand one customer's whole workspace the founder's tier.
        return TelegramBinding(chat_id_allowlist={})


@dataclass(frozen=True)
class HttpPlugin:
    """Plugin two: a plain bearer token, for the companion app, a web
    widget, or any customer calling the API directly.

    Two plugins are what proves the pattern is a contract rather than one
    special case with an interface drawn around it.
    """

    channel: str = HTTP
    task_source: TaskSource = TaskSource.api
    header_name: str = "Authorization"
    scheme: str = "Bearer "

    def present_credential(
        self, headers: Mapping[str, str], raw_body: bytes
    ) -> str | None:
        raw = _header(headers, self.header_name) or ""
        if not raw.lower().startswith(self.scheme.lower()):
            return None
        return raw[len(self.scheme) :].strip() or None

    def verify(self, presented: str, secret: str) -> bool:
        return hmac.compare_digest(presented, secret)

    def binding(self) -> SurfaceAdapter:
        return HttpBinding(principal_allowlist={})


def default_plugins() -> dict[str, Any]:
    """The channels this build serves. Adding Slack is one entry here and
    one class above; nothing outside this module changes."""
    return {TELEGRAM: TelegramPlugin(), HTTP: HttpPlugin()}
