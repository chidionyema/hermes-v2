"""Fact writes and point reads.

Every write goes through ``write_fact``, which:
1. Refuses a Fact without provenance before any I/O (models.py's
   ``__post_init__`` already makes that state unconstructible, so this is
   belt-and-braces).
2. Runs as a single statement inside a transaction, so a connection drop
   mid-write leaves either a committed row or nothing - never a partial
   one (Postgres statement atomicity; we additionally roll back
   explicitly on any exception so the connection is left clean for a
   retry rather than wedged in an open transaction).
3. Surfaces any failure as ``WriteFailedError`` rather than letting a
   raw driver exception reach the caller, so "retried or surfaced as a
   failed tool call" (the cp4 network-failure scenario) has one type to
   catch.
"""

from __future__ import annotations

import psycopg
from psycopg.rows import dict_row

from otto.memory.models import Fact, ProvenanceError
from otto.memory.vector_codec import embedding_to_literal, literal_to_embedding


class WriteFailedError(RuntimeError):
    """A fact write did not complete. The caller's contract (spec P4/
    cp4 network-failure scenario): retry, or surface as a failed tool
    call. Never assume a partial row was persisted - it wasn't."""


class DanglingReferenceError(RuntimeError):
    """A fact referenced a row that does not exist (e.g. ``superseded_by``
    naming a missing fact id). Deliberately NOT a ``WriteFailedError``:
    that class means "retry or surface", and retrying a dangling
    reference can never succeed - the caller must abort, not retry."""


def write_fact(conn: psycopg.Connection, fact: Fact) -> Fact:
    if fact.provenance is None:
        raise ProvenanceError("a fact without provenance is refused at write")

    row = fact.to_row()
    row["embedding"] = embedding_to_literal(row["embedding"])
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO otto_facts
                    (id, content, entity, attribute, value, embedding,
                     source_envelope_ulid, tier_at_capture, tainted,
                     confidence, created_at, last_verified_at, stale_after,
                     superseded_by)
                VALUES
                    (%(id)s, %(content)s, %(entity)s, %(attribute)s, %(value)s,
                     %(embedding)s::vector, %(source_envelope_ulid)s, %(tier_at_capture)s,
                     %(tainted)s, %(confidence)s, %(created_at)s,
                     %(last_verified_at)s, %(stale_after)s, %(superseded_by)s)
                """,
                row,
            )
        conn.commit()
    except psycopg.errors.NotNullViolation as exc:
        conn.rollback()
        raise ProvenanceError(
            "database refused the write: provenance column is NOT NULL"
        ) from exc
    except psycopg.errors.ForeignKeyViolation as exc:
        # The foreign key on otto_facts (superseded_by REFERENCES
        # otto_facts) is the last point where every writer merges; a
        # violation there means the fact names a row that does not exist.
        # Nothing was stored, and a retry can never succeed.
        conn.rollback()
        raise DanglingReferenceError(
            f"fact {fact.id} references a missing row; nothing was stored: {exc}"
        ) from exc
    except (psycopg.OperationalError, psycopg.errors.Error) as exc:
        try:
            conn.rollback()
        except psycopg.Error:
            pass  # connection is already gone (e.g. dropped mid-write); nothing to roll back on
        raise WriteFailedError(f"fact write did not complete: {exc}") from exc
    return fact


def get_fact(conn: psycopg.Connection, fact_id: str) -> Fact | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM otto_facts WHERE id = %s", (fact_id,))
        row = cur.fetchone()
    if not row:
        return None
    row["embedding"] = literal_to_embedding(row["embedding"])
    return Fact.from_row(row)


def count_facts_with_null_provenance(conn: psycopg.Connection) -> int:
    """Direct evidence for the cp4 edge-case scenario: after a rejected
    insert, the live table has zero rows with null provenance."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM otto_facts WHERE source_envelope_ulid IS NULL"
        )
        (count,) = cur.fetchone()
    return count
