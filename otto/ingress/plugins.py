"""Per-channel plugins: the only place a channel's name means anything.

A plugin answers four questions about one channel, and nothing else:

1. *Where does this channel put its credential?* Telegram puts a shared
   secret in the ``X-Telegram-Bot-Api-Secret-Token`` header; a generic
   HTTP caller uses ``Authorization: Bearer``; Slack signs the body.
   Each of those is a few lines here and zero lines anywhere else.
2. *Is the presented credential the right one?* A constant-time compare,
   or for a signing channel, a recomputed signature.
3. *Which surface binding turns this channel's payload into the neutral
   envelope?* The existing ``otto.surface.bindings`` are reused as-is;
   this package adds no parsing of its own.
4. *How does an answer travel back out?* The address came from this
   channel's own binding and goes back to this channel's own API, so the
   one module that already knows the channel's shape is the one that
   sends. Nothing above here learns what a chat id is.

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

from otto.boot.transport import TelegramHTTPTransport
from otto.spine.envelope import TaskSource
from otto.surface.adapter import SurfaceAdapter
from otto.surface.bindings.http import HttpBinding
from otto.surface.bindings.telegram import TelegramBinding

TELEGRAM = "telegram"
HTTP = "http"


class OutboundNotSupported(RuntimeError):
    """This channel cannot begin a message; it can only answer a request
    while the request is still open. A plain HTTP caller is the example:
    by the time an answer exists the connection that asked is long gone,
    so the answer is fetched, not pushed."""


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

    def send_reply(self, secret: str, reply_to: str, text: str) -> None:
        """Deliver one answer back to the address the binding minted.

        ``secret`` is the customer's *outbound* credential, resolved from
        ``ChannelBinding.outbound_secret_ref`` -- never the inbound one.
        Raises ``OutboundNotSupported`` on a channel that has no way to
        start a message of its own.
        """
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

    def binding(
        self, principal_allowlist: Mapping[str, str] | None = None
    ) -> SurfaceAdapter:
        # The gateway authenticates the *channel*, not the individual
        # person on the far side of it, so per-person trust cannot come
        # from the channel secret -- promoting a sender to operator on
        # the strength of one would hand a customer's whole workspace the
        # founder's tier. It comes from the binding row instead, which is
        # the same place the credential reference lives: recognising an
        # operator is onboarding, and onboarding is a database write.
        # Absent a row entry the map is empty, every sender is untrusted
        # and the taint cap applies -- the old behaviour, now a default
        # rather than the only possibility.
        chat_ids: dict[int, str] = {}
        for address, principal in (principal_allowlist or {}).items():
            try:
                chat_ids[int(address)] = principal
            except (TypeError, ValueError):
                # A malformed key is one unrecognised sender, not a broken
                # door: skipping it leaves that sender untrusted.
                continue
        return TelegramBinding(chat_id_allowlist=chat_ids)

    def send_reply(self, secret: str, reply_to: str, text: str) -> None:
        """``secret`` is the customer's bot token. It is held only for
        the length of this call, inside the transport's own field, and
        the transport never logs it (``otto.boot.transport``)."""
        TelegramHTTPTransport(token=secret).send_message(int(reply_to), text)


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

    def binding(
        self, principal_allowlist: Mapping[str, str] | None = None
    ) -> SurfaceAdapter:
        return HttpBinding(principal_allowlist=dict(principal_allowlist or {}))

    def send_reply(self, secret: str, reply_to: str, text: str) -> None:
        raise OutboundNotSupported(
            "a plain HTTP caller has no address to push an answer to; it "
            "reads the answer back by task id"
        )


def default_plugins() -> dict[str, Any]:
    """The channels this build serves. Adding Slack is one entry here and
    one class above; nothing outside this module changes."""
    return {TELEGRAM: TelegramPlugin(), HTTP: HttpPlugin()}
