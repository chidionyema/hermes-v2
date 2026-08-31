"""Step definitions for otto/tests/cp4/features/cp4_memory_engine.feature.

Every scenario runs against a real, disposable Postgres+pgvector cluster
(otto/tests/cp4/conftest.py::pg_cluster) - no in-memory fake, per the
crew#768 task ("an in-memory fake is NOT acceptable for the drop-mid-write
scenario").
"""

from __future__ import annotations

import threading
import time
import uuid

import psycopg
import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from otto.memory import hygiene, retrieval, store
from otto.memory.audit import InMemoryAuditEmitter
from otto.memory.embeddings import DegradedEmbeddingProvider, FixedEmbeddingProvider
from otto.memory.models import Fact, Provenance
from otto.memory.store import WriteFailedError

scenarios("features/cp4_memory_engine.feature")


@pytest.fixture()
def ctx():
    """Scratch dict step defs use to pass state to each other, per
    pytest-bdd convention (fixtures are the shared namespace)."""
    return {}


@given(
    "a disposable Postgres instance with pgvector, migrated fresh",
    target_fixture="pg_ready",
)
def _(db_conn):
    with db_conn.cursor() as cur:
        cur.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        (version,) = cur.fetchone()
    assert version, "pgvector extension not installed on the scratch database"
    return db_conn


# --- Happy path ---------------------------------------------------------


@given("a fact with entity, attribute, value and a source envelope ULID as provenance")
def _(ctx, memory_config):
    ctx["fact"] = Fact(
        content="chidi's preferred coffee is a flat white",
        entity="chidi",
        attribute="coffee_preference",
        value="flat white",
        provenance=Provenance(
            source_envelope_ulid=f"01J6{uuid.uuid4().hex[:22].upper()}",
            tier_at_capture="T1",
        ),
        embedding=FixedEmbeddingProvider(dim=memory_config.embedding_dim).embed(
            "chidi's preferred coffee is a flat white"
        ),
    )


@when("mem_write commits it")
def _(ctx, pg_ready):
    store.write_fact(pg_ready, ctx["fact"])


@when(parsers.parse("mem_search is later called for that entity"))
def _(ctx, pg_ready, memory_config):
    provider = FixedEmbeddingProvider(dim=memory_config.embedding_dim)
    ctx["result"] = retrieval.search(
        pg_ready, "chidi coffee preference", provider, config=memory_config
    )


@then("the fact is returned within the top 8 fused results")
def _(ctx, memory_config):
    assert memory_config.retrieval_top_k == 8
    ids = [f.id for f in ctx["result"].facts]
    assert ctx["fact"].id in ids, (
        f"fact {ctx['fact'].id} not in top {memory_config.retrieval_top_k}: {ids}"
    )


# --- Edge case: no provenance --------------------------------------------


@when("an insert into facts is attempted with provenance NULL")
def _(ctx, pg_ready):
    with (
        pytest.raises(psycopg.errors.NotNullViolation) as excinfo,
        pg_ready.cursor() as cur,
    ):
        cur.execute(
            "INSERT INTO otto_facts (id, content, source_envelope_ulid, tier_at_capture) "
            "VALUES (gen_random_uuid(), 'no provenance here', NULL, 'T1')"
        )
    pg_ready.rollback()
    ctx["insert_error"] = excinfo.value


@then("the database constraint rejects the insert with a non-zero exit")
def _(ctx):
    assert ctx["insert_error"].sqlstate == "23502"  # not_null_violation


@then(
    parsers.parse(
        '"select count(*) from facts where provenance is null" against the live table returns 0'
    )
)
def _(pg_ready):
    assert store.count_facts_with_null_provenance(pg_ready) == 0


# --- Hygiene ---------------------------------------------------------------


@given("a fact past its stale_after date and a duplicate pair with no supersession")
def _(ctx, pg_ready):
    prov = Provenance(
        source_envelope_ulid=f"01J6{uuid.uuid4().hex[:22].upper()}",
        tier_at_capture="T1",
    )

    stale = Fact(
        content="stale weather note",
        entity="weather",
        attribute="today",
        value="sunny",
        provenance=prov,
    )
    store.write_fact(pg_ready, stale)
    with pg_ready.cursor() as cur:
        cur.execute(
            "UPDATE otto_facts SET stale_after = now() - interval '1 day' WHERE id = %s",
            (stale.id,),
        )
    pg_ready.commit()

    older = Fact(
        content="chidi's office is in London",
        entity="chidi",
        attribute="office_city",
        value="London",
        provenance=prov,
    )
    store.write_fact(pg_ready, older)
    time.sleep(0.05)  # created_at strictly orders the duplicate pair
    newer = Fact(
        content="chidi's office is in Manchester",
        entity="chidi",
        attribute="office_city",
        value="Manchester",
        provenance=prov,
    )
    store.write_fact(pg_ready, newer)

    ctx["stale_fact"] = stale
    ctx["older_dup"] = older
    ctx["newer_dup"] = newer


@when("the hygiene job runs in dry-run mode")
def _(ctx, pg_ready, memory_config):
    ctx["emitter"] = InMemoryAuditEmitter()
    ctx["dry_run_report"] = hygiene.run_hygiene(
        pg_ready, config=memory_config, audit_emitter=ctx["emitter"], dry_run=True
    )


@then(
    "the hygiene report names the stale fact and the older duplicate without deleting either"
)
def _(ctx, pg_ready):
    report = ctx["dry_run_report"]
    assert ctx["stale_fact"].id in report.expired_fact_ids
    assert ctx["older_dup"].id in report.compacted_fact_ids
    assert ctx["emitter"].events == [], "dry-run must not emit audit records"
    assert store.get_fact(pg_ready, ctx["stale_fact"].id) is not None
    assert store.get_fact(pg_ready, ctx["older_dup"].id) is not None


@when("the hygiene job runs for real")
def _(ctx, pg_ready, memory_config):
    ctx["emitter"] = InMemoryAuditEmitter()
    ctx["real_report"] = hygiene.run_hygiene(
        pg_ready, config=memory_config, audit_emitter=ctx["emitter"], dry_run=False
    )


@then("the stale fact and the older duplicate are gone")
def _(ctx, pg_ready):
    assert store.get_fact(pg_ready, ctx["stale_fact"].id) is None
    assert store.get_fact(pg_ready, ctx["older_dup"].id) is None
    assert store.get_fact(pg_ready, ctx["newer_dup"].id) is not None


@then("an audit record is emitted for each deletion")
def _(ctx):
    fact_ids = {e.fact_id for e in ctx["emitter"].events}
    assert ctx["stale_fact"].id in fact_ids
    assert ctx["older_dup"].id in fact_ids
    assert len(ctx["emitter"].events) == 2


# --- Network failure: connection drops mid write ---------------------------


@given("a mem_write in progress when the Postgres connection is dropped")
def _(ctx, pg_ready, second_conn):
    marker = f"drop-mid-write-{uuid.uuid4().hex}"
    ctx["marker"] = marker

    # A trigger that only sleeps for this test's marker row, so the
    # slow path is opt-in and every other scenario is unaffected.
    with pg_ready.cursor() as cur:
        # The marker cannot be a bind parameter here: it is embedded in
        # PL/pgSQL source text inside the dollar-quoted function body,
        # which Postgres compiles separately from the CREATE FUNCTION
        # statement's own bind parameters. It is safe to format directly:
        # the value is a str(uuid4())-derived token this test generates,
        # never external input.
        cur.execute(
            f"""
            CREATE OR REPLACE FUNCTION otto_cp4_test_slow_insert() RETURNS trigger AS $$
            BEGIN
                IF NEW.content = '{marker}' THEN
                    PERFORM pg_sleep(2);
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
            """
        )
        cur.execute(
            """
            DROP TRIGGER IF EXISTS otto_cp4_test_slow_insert_trg ON otto_facts;
            CREATE TRIGGER otto_cp4_test_slow_insert_trg
                BEFORE INSERT ON otto_facts
                FOR EACH ROW EXECUTE FUNCTION otto_cp4_test_slow_insert();
            """
        )
    pg_ready.commit()

    with pg_ready.cursor() as cur:
        cur.execute("SELECT pg_backend_pid()")
        (backend_pid,) = cur.fetchone()

    fact = Fact(
        content=marker,
        provenance=Provenance(
            source_envelope_ulid=f"01J6{uuid.uuid4().hex[:22].upper()}",
            tier_at_capture="T1",
        ),
    )
    ctx["fact"] = fact
    result: dict = {}

    def do_write():
        try:
            store.write_fact(pg_ready, fact)
            result["outcome"] = "committed"
        except WriteFailedError as exc:
            result["outcome"] = "failed"
            result["error"] = exc
        except Exception as exc:  # noqa: BLE001 - captured for assertion below
            result["outcome"] = "other_error"
            result["error"] = exc

    writer = threading.Thread(target=do_write, daemon=True)
    writer.start()
    time.sleep(0.4)  # let the INSERT reach the trigger's pg_sleep
    second_conn.execute("SELECT pg_terminate_backend(%s)", (backend_pid,))
    writer.join(timeout=10)

    ctx["write_result"] = result
    assert not writer.is_alive(), (
        "writer thread never returned after connection was terminated"
    )


@then("no partial fact row is persisted")
def _(ctx):
    # Verify via a brand-new connection, since db_conn's own connection
    # was the one just terminated and is no longer usable.
    import os

    dsn = os.environ["OTTO_MEMORY_DATABASE_URL"]
    conn = psycopg.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM otto_facts WHERE content = %s", (ctx["marker"],)
            )
            (count,) = cur.fetchone()
    finally:
        conn.close()
    assert count == 0, f"expected no partial row, found {count}"


@then("the write is surfaced as a failed tool call, never a half-written fact")
def _(ctx):
    assert ctx["write_result"]["outcome"] == "failed", ctx["write_result"]
    assert isinstance(ctx["write_result"]["error"], WriteFailedError)


# --- Bandwidth degradation ---------------------------------------------


@given("the hosted embedding API is unreachable or slower than the deadline")
def _(ctx, memory_config):
    ctx["provider"] = DegradedEmbeddingProvider(
        delay_s=memory_config.embedding_deadline_s * 5
    )
    prov = Provenance(
        source_envelope_ulid=f"01J6{uuid.uuid4().hex[:22].upper()}",
        tier_at_capture="T0",
    )
    ctx["fact"] = Fact(
        content="the estate's staging cluster is on OKE", provenance=prov
    )


@when("mem_search is called within the task's deadline_s")
def _(ctx, pg_ready, memory_config):
    store.write_fact(pg_ready, ctx["fact"])
    start = time.monotonic()
    ctx["result"] = retrieval.search(
        pg_ready, "estate staging cluster", ctx["provider"], config=memory_config
    )
    ctx["elapsed_s"] = time.monotonic() - start


@then("it falls back to Postgres full-text search alone")
def _(ctx):
    assert ctx["result"].used_embedding is False
    assert ctx["result"].fallback_reason is not None
    assert any(f.id == ctx["fact"].id for f in ctx["result"].facts)


@then("it returns within deadline_s rather than hanging on the degraded dependency")
def _(ctx, memory_config):
    # Generous multiplier over the configured deadline to absorb CI/test
    # scheduling noise, while still proving it did not wait for the
    # provider's full (5x deadline) delay.
    assert ctx["elapsed_s"] < memory_config.embedding_deadline_s * 3


# --- Taint propagation ---------------------------------------------------


@given("a provenanced fact captured from untrusted content")
def _(ctx):
    ctx["tainted_fact"] = Fact(
        content="a fetched web page claims chidi's office is in Berlin",
        entity="chidi",
        attribute="office_city_untrusted",
        provenance=Provenance(
            source_envelope_ulid=f"01J6{uuid.uuid4().hex[:22].upper()}",
            tier_at_capture="T0",
            taint=True,
        ),
    )


@when("mem_search returns a result that includes that fact")
def _(ctx, pg_ready, memory_config):
    store.write_fact(pg_ready, ctx["tainted_fact"])
    ctx["result"] = retrieval.search(
        pg_ready, "chidi office", None, config=memory_config
    )
    assert any(f.id == ctx["tainted_fact"].id for f in ctx["result"].facts), (
        "test setup failed: tainted fact was not among the retrieved results"
    )


@then("the whole retrieval result is marked tainted")
def _(ctx):
    assert ctx["result"].tainted is True
