"""``python -m otto.memory.migrate``: bring the fact store's schema up.

``otto/memory/db.py`` has applied migrations idempotently since CP4, but
only ever from a test fixture -- there was no way to run it against a
real database, which is part of why the store was never wired up in
production. This is that entry point and nothing more: it opens the
env-configured connection, applies every ``*.sql`` in name order, prints
what it applied, and exits non-zero if it could not.

Idempotent by construction: each file's SQL is ``IF NOT EXISTS`` /
``OR REPLACE`` and ``otto_schema_migrations`` records what has already
run, so a second invocation prints nothing and succeeds.
"""

from __future__ import annotations

import logging
import sys

from otto.memory import db
from otto.memory.config import load_config

_LOG = logging.getLogger(__name__)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    config = load_config()
    try:
        with db.connect(config) as conn:
            applied = db.apply_migrations(conn, config)
    except Exception as exc:  # noqa: BLE001 - this is a CLI boundary: the
        # operator needs the reason on stderr and a non-zero status, not a
        # traceback shape. The most likely one by far is that the role
        # cannot CREATE EXTENSION vector, which is a grant to fix, not a
        # code change.
        print(f"migration failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if applied:
        print("applied: " + ", ".join(applied))
    else:
        print("already up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
