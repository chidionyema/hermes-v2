"""The ``channel_binding`` table: which customer owns which channel.

This is the whole of channel onboarding. Connecting a customer's Slack,
Teams or Telegram workspace writes one row here; disconnecting it flips
one column. No deployment changes, no pod restarts, no pipeline runs —
the gateway reads the table on every request, so the row is live the
moment the transaction commits.

**Secret material never lands in this table.** A row carries
``secret_ref``, a pointer such as ``vault://otto/acme/telegram`` that
``otto.ingress.secrets`` resolves at request time, plus
``token_fingerprint``, the SHA-256 of the credential. The fingerprint is
what makes lookup a single indexed read rather than a scan that decrypts
every customer's secret in turn, and it is one-way: an attacker who reads
the whole table still cannot talk to the gateway.

Two implementations, one interface:

* ``SqliteChannelBindingStore`` — the real code path, running on SQLite.
  The same SQL runs on PostgreSQL; ``POSTGRES_DDL`` below is the
  production schema, and the only dialect difference is the timestamp
  type, which is why the DDL is written out separately rather than
  generated. Tests bind SQLite in memory, so they exercise the actual
  queries rather than a hand-written stand-in that could drift.
* ``ChannelBindingStore`` — the Protocol the gateway depends on, so a
  future asyncpg-backed store drops in without touching the gateway.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from typing import Protocol

#: The production schema. Deliberately written out rather than generated
#: from the SQLite one: a reader onboarding a customer needs to see the
#: real table, and a generated schema hides the type choices.
POSTGRES_DDL = """
CREATE TABLE IF NOT EXISTS channel_binding (
    tenant_id         TEXT        NOT NULL,
    channel           TEXT        NOT NULL,
    external_id       TEXT        NOT NULL,
    secret_ref        TEXT        NOT NULL,
    token_fingerprint TEXT        NOT NULL,
    status            TEXT        NOT NULL DEFAULT 'active',
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    outbound_secret_ref TEXT,
    PRIMARY KEY (channel, external_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS channel_binding_lookup
    ON channel_binding (channel, token_fingerprint);
CREATE INDEX IF NOT EXISTS channel_binding_tenant
    ON channel_binding (channel, tenant_id);
-- Added after the table shipped, so a running gateway's table gains the
-- column without a migration tool: the outbound reference is what the
-- answering side resolves to talk back to the customer, and a door that
-- can only listen is half a channel.
ALTER TABLE channel_binding ADD COLUMN IF NOT EXISTS outbound_secret_ref TEXT;
"""

_SQLITE_DDL = """
CREATE TABLE IF NOT EXISTS channel_binding (
    tenant_id         TEXT NOT NULL,
    channel           TEXT NOT NULL,
    external_id       TEXT NOT NULL,
    secret_ref        TEXT NOT NULL,
    token_fingerprint TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'active',
    outbound_secret_ref TEXT,
    PRIMARY KEY (channel, external_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS channel_binding_lookup
    ON channel_binding (channel, token_fingerprint);
CREATE INDEX IF NOT EXISTS channel_binding_tenant
    ON channel_binding (channel, tenant_id);
"""

ACTIVE = "active"
DISABLED = "disabled"


def fingerprint(credential: str) -> str:
    """The one-way index key for a credential.

    SHA-256 and nothing else: this value is an index key, not a password
    hash. It never leaves the server, it is never compared against
    attacker-supplied data as an authentication decision on its own (the
    channel plugin still verifies the credential itself against the
    resolved secret), and a slow hash here would put a key-derivation
    cost on every inbound webhook for no gain.
    """
    return hashlib.sha256(credential.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ChannelBinding:
    """One customer's connection to one channel."""

    tenant_id: str
    channel: str
    external_id: str
    secret_ref: str
    status: str = ACTIVE
    #: The reference to the credential this platform presents when it
    #: talks *out* on this channel -- a Telegram bot token, a Slack bot
    #: token. Separate from ``secret_ref``, which is the credential the
    #: channel presents when it talks *in*: on Telegram those are two
    #: different values (the webhook's shared secret and the bot token),
    #: and conflating them would mean either accepting the bot token as
    #: an inbound password or being unable to answer. ``None`` is a
    #: listen-only connection, which is a legitimate state, not an error.
    outbound_secret_ref: str | None = None

    def __post_init__(self) -> None:
        for name in ("tenant_id", "channel", "external_id", "secret_ref"):
            if not str(getattr(self, name) or "").strip():
                raise ValueError(f"ChannelBinding.{name} must be a non-empty string")

    @property
    def active(self) -> bool:
        return self.status == ACTIVE


class ChannelBindingStore(Protocol):
    """What the gateway asks of any binding store."""

    def register(self, binding: ChannelBinding, credential: str) -> None:
        """Add or replace one customer's channel connection. This is the
        whole of onboarding; it takes effect on the next request."""
        ...

    def find_by_credential(
        self, channel: str, credential: str
    ) -> ChannelBinding | None:
        """The customer that presented this credential on this channel,
        or ``None``. One indexed read."""
        ...

    def find_by_tenant(self, channel: str, tenant_id: str) -> ChannelBinding | None:
        """The customer's connection on this channel, looked up by who
        they are rather than by what they presented. This is the read the
        answering side does: it holds a task envelope naming a tenant, and
        needs that tenant's outbound credential reference."""
        ...

    def set_status(self, channel: str, external_id: str, status: str) -> None:
        """Enable or disable a connection without deleting its history."""
        ...


class SqliteChannelBindingStore:
    """``ChannelBindingStore`` on SQLite. ``":memory:"`` for tests, a file
    path for a local run; the same SQL runs on PostgreSQL through an
    asyncpg-backed sibling when the platform database is wired up."""

    def __init__(self, database: str = ":memory:") -> None:
        self._conn = sqlite3.connect(database, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SQLITE_DDL)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def register(self, binding: ChannelBinding, credential: str) -> None:
        if not credential:
            raise ValueError(
                "a channel binding needs the credential its channel will "
                "present, so the gateway can recognise it later"
            )
        self._conn.execute(
            "INSERT INTO channel_binding "
            "(tenant_id, channel, external_id, secret_ref, token_fingerprint, "
            "status, outbound_secret_ref) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (channel, external_id) DO UPDATE SET "
            "tenant_id = excluded.tenant_id, "
            "secret_ref = excluded.secret_ref, "
            "token_fingerprint = excluded.token_fingerprint, "
            "status = excluded.status, "
            "outbound_secret_ref = excluded.outbound_secret_ref",
            (
                binding.tenant_id,
                binding.channel,
                binding.external_id,
                binding.secret_ref,
                fingerprint(credential),
                binding.status,
                binding.outbound_secret_ref,
            ),
        )
        self._conn.commit()

    def find_by_credential(
        self, channel: str, credential: str
    ) -> ChannelBinding | None:
        row = self._conn.execute(
            "SELECT tenant_id, channel, external_id, secret_ref, status, "
            "outbound_secret_ref "
            "FROM channel_binding WHERE channel = ? AND token_fingerprint = ?",
            (channel, fingerprint(credential)),
        ).fetchone()
        return self._row_to_binding(row)

    def find_by_tenant(self, channel: str, tenant_id: str) -> ChannelBinding | None:
        row = self._conn.execute(
            "SELECT tenant_id, channel, external_id, secret_ref, status, "
            "outbound_secret_ref "
            "FROM channel_binding WHERE channel = ? AND tenant_id = ?",
            (channel, tenant_id),
        ).fetchone()
        return self._row_to_binding(row)

    @staticmethod
    def _row_to_binding(row) -> ChannelBinding | None:
        if row is None:
            return None
        return ChannelBinding(
            tenant_id=row["tenant_id"],
            channel=row["channel"],
            external_id=row["external_id"],
            secret_ref=row["secret_ref"],
            status=row["status"],
            outbound_secret_ref=row["outbound_secret_ref"],
        )

    def set_status(self, channel: str, external_id: str, status: str) -> None:
        self._conn.execute(
            "UPDATE channel_binding SET status = ? WHERE channel = ? AND external_id = ?",
            (status, channel, external_id),
        )
        self._conn.commit()
