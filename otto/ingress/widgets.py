"""Native Telegram message widgets in, founder-authenticated briefs out.

The TeleBot API 10.4 ``widget_query`` arrives as a *distinct* update kind:
not ``message`` (which the answering lane treats as an instruction), but a
``widget_query`` object the founder's tap on the private-office widget
produces. This module is the channel-neutral core of handling it, exactly
as ``otto.ingress.media`` is for voice/photo: pure detection off the JSON
the webhook already handed us, and a seam the gateway calls synchronously,
because Telegram gives the bot 15 seconds to ``answerWidgetQuery`` before
the tap is dropped — long before the async answering lane would ever run.

Everything here is pure and import-safe:

* it imports no third party and no fork tool, and
* ``detect_widget_query`` reads only the JSON a webhook already handed us —
  no socket opens in here.

The concrete ``answerWidgetQuery`` call lives in ``otto.boot.transport``
(``answer_widget_query``) and is reached through the channel plugin, which
is the one place a channel name means anything — this module decides *what*
to answer, never *how* to send it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

#: The one query the founder's widget ever asks for. Any other value is
#: ignored — ``query.data`` is attacker-controlled text, and broadening
#: the accepted set below is a denial-of-service surface, not a feature.
BRIEF_QUERY = "brief"

#: The widget id the founder's compass is registered under. Telegram
#: requires a unique widget id per widget; this is the one the finish-widget
#: (hermes-agent PR #106) declares.
FOUNDER_COMPASS_WIDGET_ID = "otto_founder_compass"


@dataclass(frozen=True)
class WidgetQuery:
    """A founder widget tap, reduced to the fields the door must check.

    ``id`` is the Telegram-issued id ``answerWidgetQuery`` echoes back.
    ``from_id`` and ``chat_id`` are the two identities the allowlist must
    both recognise (a forwarded or group view has a third ``view_chat_id``
    that is *not* trusted here). ``data`` is the string the widget sent,
    which must be exactly ``brief``.
    """

    id: str
    from_id: int
    chat_id: int
    data: str
    message_id: int | None = None
    chat_type: str = "private"


def detect_widget_query(native_event: Mapping[str, Any]) -> WidgetQuery | None:
    """Decide, purely from the parsed update, whether it is a widget tap.

    A ``widget_query`` update carries ``{id, data, 'from': {...}, view:
    {'chat': {id}}...}``; a message update carries ``message``. Only the
    former is a widget, and only when its identity fields are present and
    well-typed — a malformed object is ``None`` (nothing to act on), never
    a broken ``WidgetQuery`` the caller then half-authenticates.
    """
    query = native_event.get("widget_query")
    if not isinstance(query, dict):
        return None
    qid = query.get("id")
    data = query.get("data")
    sender = query.get("from")
    chat = query.get("chat")
    if not isinstance(qid, str) or not qid:
        return None
    if not isinstance(data, str):
        return None
    from_id = sender.get("id") if isinstance(sender, dict) else None
    chat_id = chat.get("id") if isinstance(chat, dict) else None
    if (
        type(from_id) is not int or type(chat_id) is not int
        or from_id <= 0 or chat_id <= 0
    ):
        return None
    # Telegram widgets may be displayed in other contexts. An original
    # founder-private widget alone is eligible for the private ledger.
    chat_type = chat.get("type", "") if isinstance(chat, dict) else ""
    if chat_type != "private" or query.get("inline_message_id"):
        return None
    message_id = query.get("message_id")
    if type(message_id) is not int or message_id <= 0:
        message_id = None
    return WidgetQuery(
        id=qid, from_id=from_id, chat_id=chat_id, data=data,
        message_id=message_id, chat_type=chat_type,
    )


class FounderEvidence(Protocol):
    """The seam that turns verified estate evidence into the widget's
    ``chat_data`` (see how the answering lane's registry and the media
    enrichment are injected the same way). The gateway holds one; a door
    without one answers the brief with a fail-closed "no evidence" packet
    rather than inventing numbers, and a test injects a stub so no
    catalogue read ever opens in the suite."""

    def brief(self) -> dict[str, Any]:
        """Return the ``chat_data`` object the widget renders: revision
        ``r``, timestamp ``at``, and the verified counts/headline/priority/
        id/proof. Must never raise — a down evidence source is a stale
        brief, not a broken door."""
        ...


def founder_authorized(
    query: WidgetQuery, principal_allowlist: Mapping[str, str]
) -> bool:
    """Whether the widget tap belongs to the founder on this channel.

    The allowlist maps a chat id (string) to a principal name. Both the
    sender id and the chat id must be present and resolve to the founder
    principal — a tap whose *sender* is the founder but whose *view* is a
    group or forward is not honoured. Absent either, this returns False:
    the default is untrusted, and an attacker must not learn anything by
    probing.
    """
    sender = principal_allowlist.get(str(query.from_id))
    chat = principal_allowlist.get(str(query.chat_id))
    return (
        query.chat_type == "private"
        and sender is not None and chat is not None and sender == chat
    )


def is_brief_query(query: WidgetQuery) -> bool:
    """Only the exact literal the widget sends is acted on. Anything else
    — including a lookalike with different case or padding — is ignored."""
    return query.data == BRIEF_QUERY
