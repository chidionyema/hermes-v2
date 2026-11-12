"""Cloud gateway startup delivery of the founder's native Telegram widget.

The Kubernetes Otto ingress already has the founder's bot token, private chat
and webhook binding; this uses those exact mounted identities, never an
assistant session or a manually copied credential. Delivery is recorded in
the existing gateway Postgres database to avoid re-sending on normal restarts.

A Telegram send and a database commit cannot be one atomic transaction.
A failed/ambiguous send remains a non-success state; no fake VERIFIED receipt.
A stale claim may be retried only after its 10-minute recovery fence.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time
from pathlib import Path
from typing import Any, Mapping

import psycopg

from otto.boot.transport import TelegramHTTPTransport
from otto.ingress.gateway import EventGateway


_CREATE = """
CREATE TABLE IF NOT EXISTS otto_founder_widget_delivery (
    delivery_key TEXT PRIMARY KEY,
    state TEXT NOT NULL CHECK (state IN ('PENDING','FAILED','DELIVERED')),
    message_id BIGINT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

_CLAIM = """
INSERT INTO otto_founder_widget_delivery (delivery_key, state)
VALUES (%s, 'PENDING')
ON CONFLICT (delivery_key) DO UPDATE
SET state = 'PENDING', updated_at = now()
WHERE otto_founder_widget_delivery.state = 'FAILED'
   OR (otto_founder_widget_delivery.state = 'PENDING'
       AND otto_founder_widget_delivery.updated_at < now() - interval '10 minutes')
RETURNING delivery_key
"""

_RECEIPT = """
UPDATE otto_founder_widget_delivery
SET state = %s, message_id = %s, updated_at = now()
WHERE delivery_key = %s AND state = 'PENDING'
"""


def auto_publish_widget(
    gateway: EventGateway, *, dsn: str, environ: Mapping[str, str] | None = None
) -> dict[str, Any]:
    """Attempt one genuine founder-chat send, with DB-backed idempotence.

    Called in a daemon thread after the normal gateway has bound its server.
    Never changes the live instruction/yield ledger and never reports success
    unless Telegram returned a positive message ID AND Postgres stored it.
    """
    env = os.environ if environ is None else environ
    chat = (env.get("OTTO_OPERATOR_CHAT_ID") or "").strip()
    expected_bot = (env.get("OTTO_TELEGRAM_BOT_TOKEN") or "").strip()
    webhook = (env.get("OTTO_CHANNEL_SECRET_VAULT_OTTO_STAGING_TELEGRAM_WEBHOOK_SECRET") or "").strip()
    if not (chat.isdecimal() and expected_bot and webhook):
        return {"status": "NOT_CONFIGURED"}
    binding = gateway._store.find_by_credential("telegram", webhook)
    if (binding is None or binding.external_id != chat
        or binding.principal_allowlist.get(chat) != "founder"
        or not binding.outbound_secret_ref):
        return {"status": "REFUSED", "reason": "founder binding does not agree"}
    token = gateway._secrets.resolve(binding.outbound_secret_ref)
    if not hmac.compare_digest(token, expected_bot):
        return {"status": "REFUSED", "reason": "bot identity does not agree"}

    # Telegram widgets are immutable HTML. A delivered v1 widget cannot
    # acquire voice controls by editing chat_data. Bind idempotence to the
    # actual HTML revision, so a new version is sent exactly once.
    widget_html = Path(__file__).with_name("otto_founder_compass.html").read_bytes()
    widget_version = hashlib.sha256(widget_html).hexdigest()
    key = hashlib.sha256(
        ("otto-founder-widget-v2:" + binding.tenant_id + ":" + chat
         + ":" + token + ":" + widget_version).encode("utf-8")
    ).hexdigest()
    with psycopg.connect(dsn, connect_timeout=3) as conn:
        conn.execute(_CREATE)
        claimed = conn.execute(_CLAIM, (key,)).fetchone()
        if claimed is None:
            row = conn.execute(
                "SELECT state, message_id FROM otto_founder_widget_delivery WHERE delivery_key = %s",
                (key,),
            ).fetchone()
            if row and row[0] == "DELIVERED" and type(row[1]) is int and row[1] > 0:
                return {"status": "ALREADY_DELIVERED", "message_id": row[1]}
            return {"status": "PENDING_RECOVERY"}
    try:
        sent = TelegramHTTPTransport(token=token).send_widget(
            int(chat), "otto_founder_compass", gateway._safe_brief()
        )
        if type(sent) is not int or sent <= 0:
            raise RuntimeError("Telegram sendWidget returned no positive message_id")
    except Exception:
        # Only a FAILED Telegram send may be retried immediately. A send which
        # succeeded but lost its DB commit is ambiguous, not a failed send.
        try:
            with psycopg.connect(dsn, connect_timeout=3) as conn:
                conn.execute(_RECEIPT, ("FAILED", None, key))
        except Exception:
            return {"status": "BLIND", "reason": "send and receipt both unavailable"}
        return {"status": "FAILED", "reason": "Telegram sendWidget rejected or unavailable"}

    try:
        with psycopg.connect(dsn, connect_timeout=3) as conn:
            result = conn.execute(_RECEIPT, ("DELIVERED", sent, key))
            if result.rowcount != 1:
                return {"status": "BLIND", "reason": "Telegram sent but receipt row not updated"}
    except Exception:
        # Do NOT mark a delivered Telegram message FAILED: that would cause
        # an immediate duplicate on the next retry. Preserve PENDING for
        # bounded recovery and surface the ambiguous state explicitly.
        return {"status": "BLIND", "reason": "Telegram sent but database receipt unavailable"}
    return {"status": "DELIVERED", "message_id": sent}
