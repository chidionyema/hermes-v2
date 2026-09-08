"""The conversation survives the pod -- graded against a real Postgres.

crew#920 item 3. On 2026-09-08 the otto-gateway pod restarted at 09:22 and
the founder's 08:52 exchange with Otto was gone: it had lived only in that
container's stdout. These scenarios grade the table that replaces it, on
the same disposable Postgres the rest of CP4 uses, with the real migration
chain applied -- not a fake, because "the row is still there after the
process that wrote it is gone" is not a claim a mock can make.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from otto.memory import conversation, db as memdb

ASKED = "what did we decide about the router lanes?"
ANSWERED = "The judgment lane moved off deepseek on 2026-09-08."


def _turn(**overrides) -> conversation.Turn:
    base = dict(
        task_ulid="01JBOOTTURN00000000000001",
        tenant_id="founder",
        surface="telegram",
        asked_at=datetime(2026, 9, 8, 8, 52, tzinfo=timezone.utc),
        asked=ASKED,
        answered=ANSWERED,
        lane="judgment",
        model="minimax",
        attempts=1,
        outcome_state="completed_unverified",
        verified=True,
        claims_total=2,
        claims_clean=2,
        taint_capped=False,
        cost_usd=0.0031,
    )
    base.update(overrides)
    return conversation.Turn(**base)


@pytest.mark.cp4
def test_a_turn_outlives_the_connection_that_wrote_it(db_conn, pg_cluster) -> None:
    """The whole exchange comes back on a connection the writer never had.

    This is the scenario that failed in production: the process holding the
    conversation went away. Here the writing connection is closed outright
    and a second, independent connection reads the row back with both sides
    of the conversation and the attribution intact.
    """
    conversation.write_turn(db_conn, _turn())
    dsn = os.environ["OTTO_MEMORY_DATABASE_URL"]
    db_conn.close()

    with psycopg.connect(dsn) as reader, reader.cursor() as cur:
        cur.execute(
            "SELECT asked, answered, lane, model, attempts, outcome_state, "
            "verified, claims_total, claims_clean, tenant_id, surface "
            "FROM otto_turns WHERE task_ulid = %s",
            ("01JBOOTTURN00000000000001",),
        )
        row = cur.fetchone()

    assert row is not None, "the conversation did not survive its writer"
    assert row[0] == ASKED
    assert row[1] == ANSWERED
    assert row[2:6] == ("judgment", "minimax", 1, "completed_unverified")
    assert row[6] is True
    assert row[7:] == (2, 2, "founder", "telegram")


@pytest.mark.cp4
def test_a_replayed_task_records_one_conversation_not_two(db_conn) -> None:
    """A task redelivered off the bus after a restart records once.

    ``otto.ingress.worker`` can pull the same task twice when a pod dies
    mid-answer -- which is the exact failure this table exists because of,
    so it must not be the failure the table itself introduces.
    """
    conversation.write_turn(db_conn, _turn())
    conversation.write_turn(db_conn, _turn(answered="a second, later answer"))

    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT answered FROM otto_turns WHERE task_ulid = %s",
            ("01JBOOTTURN00000000000001",),
        )
        rows = cur.fetchall()
    assert len(rows) == 1
    assert rows[0][0] == ANSWERED, "the replay overwrote the original answer"


@pytest.mark.cp4
def test_never_judged_and_judged_unclean_are_different_rows(db_conn) -> None:
    """ "Nobody checked" and "we checked and it was thin" are distinguishable.

    crew#920 item 6 asks which answers went out unverified. A two-valued
    column cannot answer it: a task the verify lane never ran on would be
    indistinguishable from one it ran on and cleared nothing. Both must
    also be findable in one query, which is what the partial index serves.
    """
    conversation.write_turn(
        db_conn,
        _turn(
            task_ulid="01JNEVERJUDGED000000000001",
            verified=None,
            claims_clean=None,
            outcome_state="queued_budget",
        ),
    )
    conversation.write_turn(
        db_conn,
        _turn(
            task_ulid="01JJUDGEDUNCLEAN00000000001",
            verified=False,
            claims_total=3,
            claims_clean=1,
        ),
    )
    conversation.write_turn(db_conn, _turn())  # verified True

    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT task_ulid, verified, claims_clean FROM otto_turns "
            "WHERE verified IS NOT TRUE ORDER BY task_ulid"
        )
        rows = cur.fetchall()

    assert [r[0] for r in rows] == [
        "01JJUDGEDUNCLEAN00000000001",
        "01JNEVERJUDGED000000000001",
    ], "the verified-answer must not appear among the unverified"
    judged_unclean, never_judged = rows
    assert judged_unclean[1] is False and judged_unclean[2] == 1
    assert never_judged[1] is None and never_judged[2] is None


@pytest.mark.cp4
def test_a_tenants_conversation_reads_back_newest_first(db_conn) -> None:
    """The transcript query a support engineer runs returns that tenant's
    turns, newest first, and nobody else's."""
    start = datetime(2026, 9, 8, 8, 0, tzinfo=timezone.utc)
    for i, tenant in enumerate(["founder", "founder", "other-co"]):
        conversation.write_turn(
            db_conn,
            _turn(
                task_ulid=f"01JTENANT{i:016d}",
                tenant_id=tenant,
                asked=f"question {i}",
                asked_at=start + timedelta(minutes=i),
            ),
        )
    # answered_at defaults to now() for every row in this test, so order the
    # transcript by the time the question was asked to get a stable read.
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT asked FROM otto_turns WHERE tenant_id = %s ORDER BY asked_at DESC",
            ("founder",),
        )
        rows = [r[0] for r in cur.fetchall()]
    assert rows == ["question 1", "question 0"]


@pytest.mark.cp4
def test_recording_into_an_unmigrated_database_loses_the_row_not_the_answer(
    pg_cluster, memory_config, monkeypatch
) -> None:
    """A database with no ``otto_turns`` returns False and raises nothing.

    The answering path calls ``record()`` after the reply is composed. If
    this could raise, an unmigrated or half-deployed database would take
    the sender's answer with it -- trading a logging problem for an outage.
    """
    import uuid

    dbname = f"otto_noturns_{uuid.uuid4().hex[:12]}"
    pg_cluster.create_database(dbname)
    monkeypatch.setenv("OTTO_MEMORY_DATABASE_URL", pg_cluster.dsn(dbname))
    try:
        assert conversation.record(_turn()) is False
    finally:
        pg_cluster.drop_database(dbname)


@pytest.mark.cp4
def test_recording_with_no_store_configured_is_a_silent_no_op(monkeypatch) -> None:
    """No database configured at all: no connection attempt, no exception.

    This is the laptop and the test suite. ``fast_recall.configured`` is the
    same gate the fact tier uses, so memory is on or off as one thing.
    """
    monkeypatch.delenv("OTTO_MEMORY_DATABASE_URL", raising=False)
    for name in (
        "PGHOST",
        "PGPORT",
        "PGUSER",
        "PGPASSWORD",
        "PGDATABASE",
        "PGSERVICE",
        "PGDATABASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)

    def _explode(*a, **k):
        raise AssertionError("record() opened a connection with no store configured")

    monkeypatch.setattr(memdb, "connect", _explode)
    assert conversation.record(_turn()) is False


@pytest.mark.cp4
def test_the_migration_is_idempotent(db_conn, memory_config) -> None:
    """Re-applying the chain adds nothing: the deploy Job reruns on every
    restart and must not fail or duplicate on the second pass."""
    assert memdb.apply_migrations(db_conn, memory_config) == []
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM otto_schema_migrations WHERE filename = %s",
            ("0003_conversation_record.sql",),
        )
        assert cur.fetchone()[0] == 1
