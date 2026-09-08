"""Step definitions for cp4_hardening.feature, plus regression tests for
the independent verifier's findings on crew#768 (comment 5485606405):

1. Context budgets and compaction - BDD, features/cp4_hardening.feature.
2. Hygiene TTL<=0 refused by config, and the max-deletion-fraction cap.
3. Hygiene deletion and its audit row are atomic (outbox pattern).
4. An empty-string provenance ULID is refused by the schema itself, not
   only the Python layer.
5. The taint-propagation inverse: an all-clean result is not tainted.

Every test runs against the same real, disposable Postgres+pgvector
cluster as test_cp4_memory_engine.py (otto/tests/cp4/conftest.py) - no
in-memory fake.
"""

from __future__ import annotations

import os
import uuid

import psycopg
import pytest
from pytest_bdd import given, scenarios, then, when

from otto.memory import context, hygiene, retrieval, store
from otto.memory.audit import InMemoryAuditEmitter
from otto.memory.config import ConfigError, load_config
from otto.memory.models import Fact, Provenance

scenarios("features/cp4_hardening.feature")


def _prov(taint: bool = False, tier: str = "T1") -> Provenance:
    return Provenance(
        source_envelope_ulid=f"01J6{uuid.uuid4().hex[:22].upper()}",
        tier_at_capture=tier,
        taint=taint,
    )


@pytest.fixture()
def ctx():
    return {}


# --- Context budget / compaction scenarios --------------------------------


@given(
    "three short facts and a context budget large enough for all of them",
    target_fixture="budget_setup",
)
def _(memory_config):
    facts = [
        Fact(content="fact one is short", provenance=_prov()),
        Fact(content="fact two is short", provenance=_prov()),
        Fact(content="fact three is short", provenance=_prov()),
    ]
    total_tokens = sum(
        context.estimate_tokens(f.content, memory_config.context_chars_per_token)
        for f in facts
    )
    return {"facts": facts, "budget": total_tokens + 10}


@given(
    "five facts and a context budget that only fits the first two",
    target_fixture="budget_setup",
)
def _(memory_config):
    facts = [Fact(content="alpha fact " * 5, provenance=_prov()) for _ in range(5)]
    per_fact = context.estimate_tokens(
        facts[0].content, memory_config.context_chars_per_token
    )
    # room for exactly the first two, not a third
    return {"facts": facts, "budget": per_fact * 2}


@when("the context is assembled", target_fixture="assembly_result")
def _(ctx, budget_setup, memory_config):
    result = context.assemble_context(
        budget_setup["facts"],
        config=memory_config,
        budget_tokens=budget_setup["budget"],
    )
    ctx["assembly"] = result
    ctx["facts"] = budget_setup["facts"]
    return result


@when(
    "the context is assembled and the dropped facts are compacted",
    target_fixture="assembly_result",
)
def _(ctx, budget_setup, memory_config, db_conn):
    for fact in budget_setup["facts"]:
        store.write_fact(db_conn, fact)
    result = context.assemble_context(
        budget_setup["facts"],
        config=memory_config,
        budget_tokens=budget_setup["budget"],
    )
    ctx["assembly"] = result
    ctx["facts"] = budget_setup["facts"]
    ctx["emitter"] = InMemoryAuditEmitter()
    ctx["compaction"] = context.compact_over_budget(
        db_conn, result, config=memory_config, audit_emitter=ctx["emitter"]
    )
    return result


@then("all three facts are included and the assembly is not truncated")
def _(ctx):
    assert len(ctx["assembly"].facts) == 3
    assert ctx["assembly"].truncated is False
    assert ctx["assembly"].dropped_fact_ids == []


@then("only the facts that fit are included and truncated is true")
def _(ctx):
    assert len(ctx["assembly"].facts) == 2
    assert ctx["assembly"].truncated is True


@then("every fact left out is named in the dropped list")
def _(ctx):
    included_ids = {f.id for f in ctx["assembly"].facts}
    all_ids = {f.id for f in ctx["facts"]}
    expected_dropped = all_ids - included_ids
    assert set(ctx["assembly"].dropped_fact_ids) == expected_dropped
    assert len(expected_dropped) == 3


@then("a summary fact is written for the compacted facts")
def _(ctx):
    assert ctx["compaction"].compacted is True
    assert ctx["compaction"].summary_fact_id is not None


@then(
    "every compacted fact's row still exists, linked to the summary via superseded_by"
)
def _(ctx, db_conn):
    summary_id = ctx["compaction"].summary_fact_id
    for fact_id in ctx["compaction"].compacted_fact_ids:
        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT superseded_by FROM otto_facts WHERE id = %s", (fact_id,)
            )
            row = cur.fetchone()
        assert row is not None, f"compacted fact {fact_id} row was destroyed"
        assert str(row[0]) == summary_id


@then("the compaction is audited")
def _(ctx):
    assert len(ctx["emitter"].events) == 1
    event = ctx["emitter"].events[0]
    assert event.action == "context_compact"
    assert event.fact_id == ctx["compaction"].summary_fact_id


@then("a fresh search no longer returns the compacted facts directly")
def _(ctx, db_conn, memory_config):
    result = retrieval.search(db_conn, "alpha fact", None, config=memory_config)
    returned_ids = {f.id for f in result.facts}
    assert not (returned_ids & set(ctx["compaction"].compacted_fact_ids))


# --- Hygiene: TTL<=0 refused by config -------------------------------------


def test_ttl_zero_is_refused_by_config_before_hygiene_ever_runs():
    os.environ["OTTO_MEMORY_DEFAULT_TTL_DAYS"] = "0"
    try:
        with pytest.raises(ConfigError, match="default_ttl_days"):
            load_config()
    finally:
        del os.environ["OTTO_MEMORY_DEFAULT_TTL_DAYS"]


def test_ttl_negative_is_also_refused():
    os.environ["OTTO_MEMORY_DEFAULT_TTL_DAYS"] = "-5"
    try:
        with pytest.raises(ConfigError, match="default_ttl_days"):
            load_config()
    finally:
        del os.environ["OTTO_MEMORY_DEFAULT_TTL_DAYS"]


# --- Hygiene: max-deletion-fraction cap -------------------------------------


def test_hygiene_stops_and_alerts_when_over_the_deletion_cap(db_conn, memory_config):
    for i in range(10):
        store.write_fact(db_conn, Fact(content=f"healthy fact {i}", provenance=_prov()))
    with db_conn.cursor() as cur:
        cur.execute("SELECT id FROM otto_facts ORDER BY created_at ASC LIMIT 5")
        stale_ids = [r[0] for r in cur.fetchall()]
        cur.execute(
            "UPDATE otto_facts SET stale_after = now() - interval '1 day' "
            "WHERE id = ANY(%s::uuid[])",
            (stale_ids,),
        )
    db_conn.commit()

    # 5/10 = 50%, over the default 20% cap.
    emitter = InMemoryAuditEmitter()
    report = hygiene.run_hygiene(
        db_conn, config=memory_config, audit_emitter=emitter, dry_run=False
    )

    assert report.capped is True
    assert report.cap_reason is not None
    assert report.total_deleted == 0
    assert emitter.events == [], "capped run must not delete or audit anything"

    with db_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM otto_facts")
        (remaining,) = cur.fetchone()
    assert remaining == 10, "capped run deleted facts anyway"

    with db_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM otto_hygiene_alerts")
        (alerts,) = cur.fetchone()
    assert alerts >= 1, "cap trip must land loudly in otto_hygiene_alerts"


def test_hygiene_proceeds_normally_when_under_the_cap(db_conn, memory_config):
    for i in range(10):
        store.write_fact(db_conn, Fact(content=f"healthy fact {i}", provenance=_prov()))
    with db_conn.cursor() as cur:
        cur.execute("SELECT id FROM otto_facts ORDER BY created_at ASC LIMIT 1")
        (stale_id,) = cur.fetchone()
        cur.execute(
            "UPDATE otto_facts SET stale_after = now() - interval '1 day' WHERE id = %s",
            (stale_id,),
        )
    db_conn.commit()

    # 1/10 = 10%, under the 20% cap.
    emitter = InMemoryAuditEmitter()
    report = hygiene.run_hygiene(
        db_conn, config=memory_config, audit_emitter=emitter, dry_run=False
    )
    assert report.capped is False
    assert report.total_deleted == 1
    assert len(emitter.events) == 1


# --- Hygiene: delete and audit are atomic (outbox pattern) -----------------


class _ExplodingEmitter:
    def emit(self, event):
        raise RuntimeError("audit sink down")


def test_hygiene_never_leaves_a_deletion_unaudited_when_the_emitter_fails(
    db_conn, memory_config
):
    fact = Fact(content="stale doomed fact", provenance=_prov())
    store.write_fact(db_conn, fact)
    # Padding so the one deletion candidate stays under the default 20%
    # max-deletion-fraction cap (otto/memory/hygiene.py) - this test is
    # about audit atomicity, not the cap (that has its own tests above).
    for i in range(9):
        store.write_fact(
            db_conn, Fact(content=f"unrelated healthy fact {i}", provenance=_prov())
        )
    db_conn.execute(
        "UPDATE otto_facts SET stale_after = now() - interval '1 day' WHERE id = %s",
        (fact.id,),
    )
    db_conn.commit()

    with pytest.raises(RuntimeError, match="audit sink down"):
        hygiene.run_hygiene(
            db_conn,
            config=memory_config,
            audit_emitter=_ExplodingEmitter(),
            dry_run=False,
        )

    gone = store.get_fact(db_conn, fact.id) is None
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM otto_fact_audit WHERE fact_id = %s", (fact.id,)
        )
        (audit_rows,) = cur.fetchone()

    assert not (gone and audit_rows == 0), (
        "DEFECT: hygiene deletion committed before its audit row - a "
        "failing emitter must never leave an unaudited deletion"
    )
    # The outbox pattern guarantees more than "not both bad": since the
    # audit row is written in the same transaction as the delete, a
    # committed deletion always has its audit row too.
    if gone:
        assert audit_rows == 1


# --- Schema-level refusal of an empty-string provenance ULID ---------------


def test_empty_string_ulid_refused_by_schema_check(db_conn):
    with pytest.raises(psycopg.errors.CheckViolation):
        db_conn.execute(
            "INSERT INTO otto_facts (id, content, source_envelope_ulid, tier_at_capture) "
            "VALUES (gen_random_uuid(), 'empty ulid probe', '', 'T1')"
        )
    db_conn.rollback()
    with db_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM otto_facts WHERE source_envelope_ulid = ''")
        (n,) = cur.fetchone()
    assert n == 0


def test_garbage_shaped_ulid_refused_by_schema_check(db_conn):
    with pytest.raises(psycopg.errors.CheckViolation):
        db_conn.execute(
            "INSERT INTO otto_facts (id, content, source_envelope_ulid, tier_at_capture) "
            "VALUES (gen_random_uuid(), 'short ulid probe', 'not-a-ulid', 'T1')"
        )
    db_conn.rollback()


# --- Taint inverse: an all-clean result is not tainted ---------------------


def test_taint_inverse_all_clean_result_is_not_tainted(db_conn, memory_config):
    store.write_fact(
        db_conn, Fact(content="the sun rises in the east", provenance=_prov(False))
    )
    store.write_fact(
        db_conn, Fact(content="the sun sets in the west", provenance=_prov(False))
    )
    result = retrieval.search(db_conn, "sun rises east", None, config=memory_config)
    assert len(result.facts) >= 1
    assert result.tainted is False, "one-sided bound: an all-clean result read tainted"
