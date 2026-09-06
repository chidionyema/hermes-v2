"""Hybrid retrieval: pgvector dense search fused with Postgres full-text
search, with automatic fallback to lexical-only search when the
embedding provider is absent or degraded (cp4's bandwidth-degradation
scenario), and taint propagation across the returned result set (cp4's
taint scenario, spec section 10).

Fusion is reciprocal rank fusion (Cormack, Clarke & Buettcher, SIGIR
2009) - a standard, well-documented technique, not a new invention, and
it needs no reranker service to produce a single ordered list from two
ranked lists.
"""

from __future__ import annotations

import concurrent.futures
from dataclasses import dataclass

import psycopg
from psycopg.rows import dict_row

from otto.memory.config import MemoryConfig, load_config
from otto.memory.embeddings import EmbeddingProvider
from otto.memory.models import Fact
from otto.memory.vector_codec import embedding_to_literal, literal_to_embedding


@dataclass(frozen=True)
class RetrievalResult:
    facts: list[Fact]
    tainted: bool
    used_embedding: bool
    fallback_reason: str | None


def _row_to_fact(row: dict) -> Fact:
    row = dict(row)
    row["embedding"] = literal_to_embedding(row.get("embedding"))
    return Fact.from_row(row)


def _try_embed_within_deadline(
    provider: EmbeddingProvider, text: str, deadline_s: float
) -> list[float] | None:
    """Enforce the deadline from the *caller's* side: a degraded or slow
    provider must never make ``mem_search`` hang past ``deadline_s``
    (cp4: "it returns within deadline_s rather than hanging on the
    degraded dependency"). A provider that raises is treated the same as
    one that times out - both mean "not available right now"."""
    # Not a context manager: ThreadPoolExecutor.__exit__ calls
    # shutdown(wait=True), which would block this function until the
    # slow/hung provider call finishes - defeating the deadline. The pool
    # (and its one thread) is left to be garbage-collected once the call
    # eventually returns; that is bounded work, not a leak.
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = pool.submit(provider.embed, text)
    try:
        return future.result(timeout=deadline_s)
    except Exception:  # noqa: BLE001 - deliberately broad: a pluggable,
        # untrusted-vendor provider (LAW 34) can fail in ways this core
        # cannot enumerate; any failure or timeout degrades to the FTS
        # fallback rather than propagating and breaking mem_search.
        return None
    finally:
        pool.shutdown(wait=False)


def _lexical_search(
    conn: psycopg.Connection, query_text: str, limit: int
) -> list[Fact]:
    # Any term, not every term. ``plainto_tsquery`` ANDs its lexemes
    # together, which is right for a search box and wrong for the thing
    # this arm is actually given: a sentence somebody typed at a chat bot.
    # "what colour is the sky" against a stored "the sky is green" matched
    # nothing under AND, because the fact does not contain the word
    # "colour" -- so the lexical half of the hybrid returned empty for
    # almost every real question, and the fusion had one arm to fuse.
    # Rewriting the same lexemes with ``|`` makes it a proper ranked
    # retrieval: a fact matching one term is a weak hit, a fact matching
    # four is a strong one, and ``ts_rank`` is what tells them apart. That
    # ranking is the signal reciprocal rank fusion consumes; an AND filter
    # gives it nothing to rank.
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            WITH q AS (
                SELECT to_tsquery(
                    'english',
                    array_to_string(
                        tsvector_to_array(to_tsvector('english', %(q)s)), ' | '
                    )
                ) AS tsq
            )
            SELECT f.*, ts_rank(f.content_tsv, q.tsq) AS _score
            FROM otto_facts f, q
            WHERE q.tsq IS NOT NULL
              AND f.content_tsv @@ q.tsq
              AND f.superseded_by IS NULL
            ORDER BY _score DESC
            LIMIT %(limit)s
            """,
            {"q": query_text, "limit": limit},
        )
        rows = cur.fetchall()
    return [_row_to_fact(row) for row in rows]


def _vector_search(
    conn: psycopg.Connection, embedding: list[float], limit: int
) -> list[Fact]:
    literal = embedding_to_literal(embedding)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT *
            FROM otto_facts
            WHERE embedding IS NOT NULL
              AND superseded_by IS NULL
            ORDER BY embedding <=> %(q)s::vector
            LIMIT %(limit)s
            """,
            {"q": literal, "limit": limit},
        )
        rows = cur.fetchall()
    return [_row_to_fact(row) for row in rows]


def _reciprocal_rank_fusion(ranked_lists: list[list[Fact]], k: int) -> list[Fact]:
    scores: dict[str, float] = {}
    fact_by_id: dict[str, Fact] = {}
    for ranked in ranked_lists:
        for rank, fact in enumerate(ranked, start=1):
            fact_by_id[fact.id] = fact
            scores[fact.id] = scores.get(fact.id, 0.0) + 1.0 / (k + rank)
    ordered_ids = sorted(scores, key=lambda fid: scores[fid], reverse=True)
    return [fact_by_id[fid] for fid in ordered_ids]


def search(
    conn: psycopg.Connection,
    query_text: str,
    embedding_provider: EmbeddingProvider | None,
    config: MemoryConfig | None = None,
    deadline_s: float | None = None,
) -> RetrievalResult:
    """``mem_search``'s core: fused vector+lexical retrieval when an
    embedding is available within the deadline, lexical-only otherwise.
    """
    config = config or load_config()
    deadline = deadline_s if deadline_s is not None else config.embedding_deadline_s

    embedding: list[float] | None = None
    fallback_reason: str | None = None
    if embedding_provider is None:
        fallback_reason = "no embedding provider configured"
    else:
        embedding = _try_embed_within_deadline(embedding_provider, query_text, deadline)
        if embedding is None:
            fallback_reason = (
                "embedding provider unreachable, degraded or slower than "
                f"the {deadline}s deadline"
            )

    lexical = _lexical_search(conn, query_text, config.retrieval_candidate_pool)

    if embedding is not None:
        vector = _vector_search(conn, embedding, config.retrieval_candidate_pool)
        fused = _reciprocal_rank_fusion([vector, lexical], k=config.rrf_k)
        used_embedding = True
    else:
        fused = lexical
        used_embedding = False

    top = fused[: config.retrieval_top_k]
    # Taint propagation (cp4 taint scenario): a result carrying any
    # tainted fact marks the whole result tainted, computed here where
    # the two arms merge into one answer - not left to the caller.
    tainted = any(f.provenance.taint for f in top)

    return RetrievalResult(
        facts=top,
        tainted=tainted,
        used_embedding=used_embedding,
        fallback_reason=fallback_reason,
    )
