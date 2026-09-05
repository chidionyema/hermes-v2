"""The synchronous read path, proved against a real Postgres.

Hindsight's recall was measured at 31.87s for one query on 2026-09-05
(its own trace: no LLM on that path, ~31.7s of it a local cross-encoder
rerank inside a one-CPU container). These tests hold the replacement to
the two properties that made it worth replacing: it returns what was
stored, and it returns fast. The latency assertion is deliberately loose
compared to what the path actually costs -- it is there to catch a
regression of two orders of magnitude, not to measure a laptop.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import replace

import pytest

from otto.memory import backfill, fast_recall, store
from otto.memory.embeddings import FixedEmbeddingProvider
from otto.memory.models import Fact, Provenance

pytestmark = pytest.mark.cp4

#: Two orders of magnitude below the 31.87s that prompted this work, and
#: still far above what the path costs on a loaded CI runner.
LATENCY_CEILING_S = 3.0


def _fact(content: str, *, taint: bool = False, tier: str = "T2") -> Fact:
    return Fact(
        content=content,
        provenance=Provenance(
            source_envelope_ulid="01M1RD03B7MEB3Q2KSS315E0ZQ",
            tier_at_capture=tier,
            taint=taint,
        ),
        entity="otto/boot",
        attribute="telegram-note",
        value="env-1",
    )


def test_recall_returns_what_was_stored_and_returns_fast(db_conn, memory_config):
    store.write_fact(db_conn, _fact("the founder's cluster is called estate"))
    store.write_fact(db_conn, _fact("bananas are unrelated to anything here"))

    started = time.monotonic()
    recalled = fast_recall.recall("what is the cluster called", config=memory_config)
    elapsed = time.monotonic() - started

    assert "estate" in recalled, recalled
    assert elapsed < LATENCY_CEILING_S, f"recall took {elapsed:.3f}s"


def test_recall_works_with_no_embedding_provider_configured(db_conn, memory_config):
    """The degraded mode is a real mode: with no embedder, retrieval runs
    its Postgres full-text arm alone and still answers. This is what makes
    an unwired embedding model a slower-to-improve memory rather than an
    outage."""
    os.environ.pop("OTTO_MEMORY_EMBEDDING_URL", None)
    store.write_fact(db_conn, _fact("hindsight runs on the estate postgres"))

    recalled = fast_recall.recall("where does hindsight run", config=memory_config)

    assert "estate postgres" in recalled


def test_recall_fuses_the_vector_arm_when_an_embedder_is_present(
    db_conn, memory_config
):
    provider = FixedEmbeddingProvider(memory_config.embedding_dim)
    text = "the recall budget is thirty seconds"
    fact = replace(_fact(text), embedding=provider.embed(text))
    store.write_fact(db_conn, fact)

    recalled = fast_recall.recall(
        "recall budget", config=memory_config, embedding_provider=provider
    )

    assert "thirty seconds" in recalled


def test_a_tainted_result_says_so_in_the_text(db_conn, memory_config):
    store.write_fact(
        db_conn, _fact("an untrusted sender claimed the sky is green", taint=True)
    )

    recalled = fast_recall.recall("what colour is the sky", config=memory_config)

    assert fast_recall.TAINT_NOTE in recalled


def test_recall_is_empty_and_silent_when_no_store_is_configured(
    memory_config, monkeypatch
):
    monkeypatch.delenv("OTTO_MEMORY_DATABASE_URL", raising=False)
    for name in fast_recall._LIBPQ_ENV:
        monkeypatch.delenv(name, raising=False)

    assert fast_recall.recall("anything", config=memory_config) == ""


def test_a_dead_database_costs_no_one_their_answer(memory_config, monkeypatch, caplog):
    """The contract the whole path rests on: every failure below the call
    degrades to "no memory this turn", never to an exception reaching the
    answering lane."""
    monkeypatch.setenv(
        "OTTO_MEMORY_DATABASE_URL",
        "postgresql://nobody@127.0.0.1:1/does-not-exist?connect_timeout=1",
    )

    assert fast_recall.recall("anything", config=memory_config) == ""


def test_context_budget_bounds_what_is_returned(db_conn, memory_config):
    for i in range(40):
        store.write_fact(db_conn, _fact(f"budget fact number {i} " + "padding " * 40))

    recalled = fast_recall.recall("budget fact padding", config=memory_config)

    estimated = len(recalled) / memory_config.context_chars_per_token
    assert estimated <= memory_config.context_budget_tokens * 1.1, estimated


def test_a_fact_written_by_the_answering_lane_is_recalled_by_it(db_conn, memory_config):
    """End to end across the two halves of the change: the pipeline's own
    store write, then the pipeline's own read, with nothing in between."""
    from otto.boot import pipeline

    assert pipeline._store_fact(_fact("otto now reads memory from postgres")) is True

    recalled = fast_recall.recall(
        "where does otto read memory from", config=memory_config
    )

    assert "postgres" in recalled


def test_backfill_carries_hindsight_provenance_across(db_conn):
    item = {
        "id": "df2b2f14-4324-4645-a008-0ee9c113d2a4",
        "text": 'The name "Otto" was mentioned.',
        "entities": "Otto",
        "document_id": "769845d4-54bf-4a2b-853c-295dbcc97c02",
        "metadata": {
            "tier": "T2",
            "surface": "telegram",
            "task_id": "01M1RD03B7MEB3Q2KSS315E0ZQ",
            "taint_capped": "true",
        },
    }

    fact = backfill._to_fact(item)

    assert fact is not None
    assert fact.id == item["id"]
    assert fact.provenance.source_envelope_ulid == "01M1RD03B7MEB3Q2KSS315E0ZQ"
    assert fact.provenance.tier_at_capture == "T2"
    assert fact.provenance.taint is True


def test_backfill_never_borrows_a_task_id_it_does_not_have():
    """A memory the Architect wrote over its own plugin has no task
    metadata. It is attributed to hindsight rather than to some other
    task: provenance naming the wrong source is worse than provenance
    admitting what it is."""
    fact = backfill._to_fact({"id": "abc", "text": "written by the architect"})

    assert fact is not None
    assert fact.provenance.source_envelope_ulid == backfill.envelope_ulid_for(
        "hindsight:abc"
    )
    assert fact.provenance.source_envelope_ulid != backfill.envelope_ulid_for(
        "hindsight:abd"
    )
    assert fact.provenance.tier_at_capture == backfill.FALLBACK_TIER
    assert fact.provenance.taint is False


def test_backfill_provenance_always_fits_the_ulid_check(db_conn):
    """The live table CHECKs source_envelope_ulid to the ULID shape. The
    first live backfill wrote 4 of 693 facts because hindsight ids are
    UUIDs and cron task ids are ``cron_<hex>_<stamp>``; neither passed. A
    real ULID is kept, anything else maps to one deterministically, and
    the row must actually land in Postgres, which is where the shape is
    enforced."""
    ulid_re = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
    for source in (
        "01M1RD03B7MEB3Q2KSS315E0ZQ",
        "cron_b6b4ccf3134d_20260830_020105",
        "hindsight:2576940d-7f87-4569-a82c-f8229506343d",
    ):
        got = backfill.envelope_ulid_for(source)
        assert ulid_re.match(got), (source, got)
        assert got == backfill.envelope_ulid_for(source)
    assert (
        backfill.envelope_ulid_for("01M1RD03B7MEB3Q2KSS315E0ZQ")
        == "01M1RD03B7MEB3Q2KSS315E0ZQ"
    )

    fact = backfill._to_fact(
        {
            "id": "2576940d-7f87-4569-a82c-f8229506343d",
            "text": "User ran estate-map skill as scheduled cron job",
            "metadata": {"tier": "T2", "task_id": "cron_b6b4ccf3134d_20260830_020105"},
        }
    )
    store.write_fact(db_conn, fact)
    assert store.get_fact(db_conn, fact.id) is not None


def test_backfill_skips_a_memory_with_no_text():
    assert backfill._to_fact({"id": "abc", "text": "   "}) is None
