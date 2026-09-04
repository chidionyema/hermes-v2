"""``ChannelBindingStore`` on PostgreSQL, for the running gateway.

The SQL is the same SQL ``SqliteChannelBindingStore`` runs, with one
dialect difference: ``%s`` placeholders instead of ``?``. The column list
and the conflict target are identical, so the SQLite double the tests
bind is exercising the real shape rather than an approximation of it.

``psycopg`` and not ``asyncpg``: the gateway's request handling is
synchronous because ``http.server`` is, and a synchronous driver is the
whole requirement. ``asyncpg`` is in the estate for ``otto.spine``, which
is asynchronous for its own reasons; using it here would drag an event
loop into every lookup for no gain. Neither is a new dependency —
``psycopg==3.3.4`` is already pinned in ``otto/requirements.txt``.

There is deliberately no connection pool. ``psycopg_pool`` is a separate
distribution and adding it would be a new dependency for a door that
handles one short lookup per inbound event; a connection per call is a
few milliseconds against a Service inside the same cluster, and it means
a database restart cannot leave the gateway holding dead handles.

LAW 46: nothing here names a host, a port, a database or a password.
``dsn_from_env`` builds the connection string from the environment the
deployment sets, and reads the password from a file, because the estate's
admission policy refuses a Secret delivered as a pod environment variable.
"""

from __future__ import annotations

import os
import pathlib
from typing import Mapping

import psycopg
from psycopg.conninfo import make_conninfo

from otto.ingress.store import POSTGRES_DDL, ChannelBinding, fingerprint

HOST_ENV = "OTTO_INGRESS_DB_HOST"
PORT_ENV = "OTTO_INGRESS_DB_PORT"
NAME_ENV = "OTTO_INGRESS_DB_NAME"
USER_ENV = "OTTO_INGRESS_DB_USER"
# noqa reason: this is the NAME of an environment variable holding a file
# path, not a password. The password is only ever read from that file.
PASSWORD_FILE_ENV = "OTTO_INGRESS_DB_PASSWORD_FILE"  # noqa: S105

DEFAULT_PORT = "5432"

INSERT_SQL = (
    "INSERT INTO channel_binding "
    "(tenant_id, channel, external_id, secret_ref, token_fingerprint, "
    "status, outbound_secret_ref) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s) "
    "ON CONFLICT (channel, external_id) DO UPDATE SET "
    "tenant_id = excluded.tenant_id, "
    "secret_ref = excluded.secret_ref, "
    "token_fingerprint = excluded.token_fingerprint, "
    "status = excluded.status, "
    "outbound_secret_ref = excluded.outbound_secret_ref"
)

#: The lookup the gateway runs on every inbound event. ``channel`` is in
#: the WHERE clause and not only the fingerprint: without it, a
#: credential stolen from one channel would open a binding on another.
LOOKUP_SQL = (
    "SELECT tenant_id, channel, external_id, secret_ref, status, "
    "outbound_secret_ref "
    "FROM channel_binding WHERE channel = %s AND token_fingerprint = %s"
)

#: The read the answering side runs: it holds a task envelope naming a
#: tenant and needs that tenant's outbound credential reference. Indexed
#: by (channel, tenant_id), like the inbound lookup is by fingerprint --
#: neither path may become a scan as customers are added.
TENANT_LOOKUP_SQL = (
    "SELECT tenant_id, channel, external_id, secret_ref, status, "
    "outbound_secret_ref "
    "FROM channel_binding WHERE channel = %s AND tenant_id = %s"
)

STATUS_SQL = (
    "UPDATE channel_binding SET status = %s WHERE channel = %s AND external_id = %s"
)


class DatabaseNotConfigured(RuntimeError):
    """The deployment did not say where the binding table lives.

    Raised at boot rather than on the first inbound event: a gateway that
    starts without its table answers 401 to every real customer, which
    reads to an operator as a credential problem and is not one.
    """


def dsn_from_env(environ: Mapping[str, str] | None = None) -> str:
    """The connection string, assembled from the deployment's own values.

    The password is read from the file the platform's secret store
    projects, never from an environment variable: the estate's admission
    policy refuses a Secret delivered as pod environment, and a password
    in the environment is readable by anything that can list the process.
    """
    env = os.environ if environ is None else environ
    missing = [name for name in (HOST_ENV, NAME_ENV, USER_ENV) if not env.get(name)]
    if missing:
        raise DatabaseNotConfigured(
            "the channel binding database is not configured; set " + ", ".join(missing)
        )

    password_file = env.get(PASSWORD_FILE_ENV, "")
    if not password_file:
        raise DatabaseNotConfigured(
            f"set {PASSWORD_FILE_ENV} to the path of the projected password file"
        )
    path = pathlib.Path(password_file)
    if not path.is_file():
        raise DatabaseNotConfigured(
            f"{PASSWORD_FILE_ENV} points at {password_file}, which is not a file; "
            "the secret store has not projected the password yet"
        )

    return make_conninfo(
        host=env[HOST_ENV],
        port=int(env.get(PORT_ENV, DEFAULT_PORT)),
        dbname=env[NAME_ENV],
        user=env[USER_ENV],
        password=path.read_text().strip(),
    )


class PostgresChannelBindingStore:
    """``ChannelBindingStore`` over PostgreSQL.

    Satisfies the Protocol in ``otto.ingress.store``, so the gateway takes
    this or the SQLite store without knowing which it holds.
    """

    def __init__(self, dsn: str, *, connect_timeout: int = 5) -> None:
        self._dsn = dsn
        self._connect_timeout = connect_timeout

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(
            self._dsn, connect_timeout=self._connect_timeout, autocommit=True
        )

    def ensure_schema(self) -> None:
        """Create the table if it is not there.

        Idempotent, and safe to run from every replica at once:
        ``CREATE TABLE IF NOT EXISTS`` and ``CREATE UNIQUE INDEX IF NOT
        EXISTS`` are, and the seed that fills the table takes an advisory
        lock of its own.
        """
        with self._connect() as conn:
            conn.execute(POSTGRES_DDL)

    def register(self, binding: ChannelBinding, credential: str) -> None:
        if not credential:
            raise ValueError(
                "a channel binding needs the credential its channel will "
                "present, so the gateway can recognise it later"
            )
        with self._connect() as conn:
            conn.execute(
                INSERT_SQL,
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

    def find_by_credential(
        self, channel: str, credential: str
    ) -> ChannelBinding | None:
        with self._connect() as conn:
            row = conn.execute(
                LOOKUP_SQL, (channel, fingerprint(credential))
            ).fetchone()
        return self._row_to_binding(row)

    def find_by_tenant(self, channel: str, tenant_id: str) -> ChannelBinding | None:
        with self._connect() as conn:
            row = conn.execute(TENANT_LOOKUP_SQL, (channel, tenant_id)).fetchone()
        return self._row_to_binding(row)

    @staticmethod
    def _row_to_binding(row) -> ChannelBinding | None:
        if row is None:
            return None
        return ChannelBinding(
            tenant_id=row[0],
            channel=row[1],
            external_id=row[2],
            secret_ref=row[3],
            status=row[4],
            outbound_secret_ref=row[5],
        )

    def set_status(self, channel: str, external_id: str, status: str) -> None:
        with self._connect() as conn:
            conn.execute(STATUS_SQL, (status, channel, external_id))
