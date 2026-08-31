"""Connection and migrations for the memory store.

LAW 46: the connection is resolved from the environment only. Nothing in
this file names a host, port, account or credential as a literal.
"""

from __future__ import annotations

import os
from pathlib import Path

import psycopg

from otto.memory.config import MemoryConfig, load_config

_MIGRATIONS_PACKAGE_DIR = Path(__file__).parent / "migrations"


def get_dsn(config: MemoryConfig | None = None) -> str | None:
    """Read the connection string from the env var named in config.

    Returns None when unset, in which case ``connect()`` falls back to the
    standard libpq PG* environment variables (PGHOST, PGPORT, PGUSER,
    PGPASSWORD, PGDATABASE) - still env only, never a default value typed
    here.
    """
    config = config or load_config()
    return os.environ.get(config.database_url_env)


def connect(config: MemoryConfig | None = None, **kwargs) -> psycopg.Connection:
    """Open a connection from env-only configuration.

    Extra ``kwargs`` are forwarded to ``psycopg.connect`` (e.g.
    ``autocommit=True``); no host/port/credential is ever hardcoded here.
    """
    dsn = get_dsn(config)
    if dsn:
        return psycopg.connect(dsn, **kwargs)
    # No OTTO_MEMORY_DATABASE_URL set: fall back to libpq's own PG* env
    # vars, resolved by psycopg itself. Still nothing typed in this file.
    return psycopg.connect(**kwargs)


def migrations_dir(config: MemoryConfig | None = None) -> Path:
    config = config or load_config()
    if config.migrations_env_dir:
        return Path(config.migrations_env_dir)
    return _MIGRATIONS_PACKAGE_DIR


def apply_migrations(
    conn: psycopg.Connection, config: MemoryConfig | None = None
) -> list[str]:
    """Apply every ``*.sql`` file in the migrations directory, in name
    order, idempotently.

    Each file's own SQL is idempotent (``IF NOT EXISTS`` / ``OR
    REPLACE``); this function additionally tracks which filenames have
    already run in ``otto_schema_migrations`` so re-invocation is cheap
    and so a migration is never re-applied out of order. Returns the list
    of filenames actually applied this call (empty on a repeat call).
    """
    config = config or load_config()
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS otto_schema_migrations (
                filename TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        cur.execute("SELECT filename FROM otto_schema_migrations")
        already_applied = {row[0] for row in cur.fetchall()}
    conn.commit()

    applied: list[str] = []
    for path in sorted(migrations_dir(config).glob("*.sql")):
        if path.name in already_applied:
            continue
        sql = path.read_text().replace("{{EMBEDDING_DIM}}", str(config.embedding_dim))
        with conn.cursor() as cur:
            cur.execute(sql)  # type: ignore[arg-type]
            cur.execute(
                "INSERT INTO otto_schema_migrations (filename) VALUES (%s)",
                (path.name,),
            )
        conn.commit()
        applied.append(path.name)
    return applied
