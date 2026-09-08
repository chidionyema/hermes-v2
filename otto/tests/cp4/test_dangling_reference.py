"""Regression: a fact referencing a missing row aborts loudly, as its own
error class, and stores nothing.

Independent verifier probe (crew#768 hardening wave): ``superseded_by``
naming a fact id that does not exist used to surface as the generic,
retryable ``WriteFailedError`` — but a dangling reference can never
succeed on retry, so handing it to the retry path is a silent loop, not
an abort. It must raise ``DanglingReferenceError`` and leave the table
untouched.

Runs against the same real, disposable Postgres+pgvector cluster as the
rest of the cp4 suite (otto/tests/cp4/conftest.py) - the foreign key
being exercised is the database's own.
"""

from __future__ import annotations

import uuid

import pytest

from otto.memory import store
from otto.memory.models import Fact, Provenance


def _prov() -> Provenance:
    return Provenance(
        source_envelope_ulid=f"01J6{uuid.uuid4().hex[:22].upper()}",
        tier_at_capture="T1",
        taint=False,
    )


def test_dangling_superseded_by_raises_its_own_error_and_stores_nothing(
    db_conn,
) -> None:
    fact = Fact(
        content="this fact claims to be superseded by a row that does not exist",
        provenance=_prov(),
        superseded_by=str(uuid.uuid4()),
    )
    with pytest.raises(store.DanglingReferenceError, match="missing row"):
        store.write_fact(db_conn, fact)

    # Nothing was stored: no dangling reference, no partial row.
    with db_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM otto_facts")
        (count,) = cur.fetchone()
    assert count == 0


def test_dangling_reference_is_not_graded_as_retryable(db_conn) -> None:
    # The retry contract lives on WriteFailedError; a dangling reference
    # must not be an instance of it, or callers loop on a write that can
    # never succeed.
    assert not issubclass(store.DanglingReferenceError, store.WriteFailedError)

    # The connection is left clean for the caller's next statement.
    fact = Fact(
        content="a second write on the same connection still works afterwards",
        provenance=_prov(),
        superseded_by=str(uuid.uuid4()),
    )
    with pytest.raises(store.DanglingReferenceError):
        store.write_fact(db_conn, fact)
    stored = store.write_fact(
        db_conn,
        Fact(content="a clean fact with no references", provenance=_prov()),
    )
    assert store.get_fact(db_conn, stored.id) is not None
