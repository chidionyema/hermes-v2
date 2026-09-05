"""The synchronous read path: pgvector + full-text, fused, no reranker.

Why this file exists, measured rather than argued. Until now the
answering lane read memory by calling hindsight's ``/memories/recall``
(``otto/memory/hindsight.py``) while the sender waited. On 2026-09-05 a
traced recall against hindsight 0.9.2 on the estate cluster returned
``summary.total_duration_seconds = 31.87`` for 107 results, and 34.77s at
a lower budget -- the budget knob changes nothing. There is no language
model anywhere on that path: the trace's stages are vector search, BM25,
reciprocal rank fusion, a **local cross-encoder rerank**, then a graph
walk. The six instrumented phases sum to 0.16s, so ~31.7s is the
cross-encoder scoring roughly a hundred candidates -- a full transformer
forward pass each -- inside a container capped at ``limits.cpu: "1"``.

A cross-encoder is the right tool offline and the wrong one in front of a
person waiting in a chat. This module is the same retrieval without it:
``otto/memory/retrieval.py``'s hybrid search (pgvector dense + Postgres
full-text, fused by reciprocal rank fusion) over ``otto_facts``, bounded
by ``otto/memory/context.py``'s token budget. Two indexed queries and
some arithmetic; no model call, no reranker, no network hop beyond
Postgres.

Hindsight is not deleted and not diminished. It keeps the asynchronous
tier it is genuinely good at: consolidation, entity extraction and the
knowledge graph, running out of band on writes where 30 seconds costs
nobody anything.

The contract is deliberately identical to ``hindsight.recall``: one
string in, one string out, empty when memory is unconfigured or
unreachable, and it never raises. A memory that cannot be reached must
never cost the sender their answer.
"""

from __future__ import annotations

import logging
import os

from otto.memory import context as context_mod
from otto.memory import db, retrieval
from otto.memory.config import MemoryConfig, load_config
from otto.memory.embeddings import EmbeddingProvider
from otto.memory.embeddings_litellm import provider_from_env

_LOG = logging.getLogger(__name__)

#: libpq's own variables. If none of these and no OTTO_MEMORY_DATABASE_URL
#: are set there is no store to read, and recall is a no-op rather than a
#: connection attempt on every inbound message.
_LIBPQ_ENV = ("PGHOST", "PGDATABASE", "PGSERVICE", "PGURI")

#: Prefix on the one line that says the result set carries untrusted
#: material. retrieval.py propagates taint across a whole result; the
#: caller already labels recalled memory as background-only, and this
#: makes the stronger case visible in the text itself.
TAINT_NOTE = "(some of the following came from untrusted input)"


def configured(config: MemoryConfig | None = None) -> bool:
    """True when this process has somewhere to read facts from."""
    config = config or load_config()
    if db.get_dsn(config):
        return True
    return any(os.environ.get(name) for name in _LIBPQ_ENV)


def _render(facts, tainted: bool) -> str:
    lines = [TAINT_NOTE] if tainted else []
    for fact in facts:
        text = fact.content.strip()
        if fact.entity and fact.attribute and fact.value:
            text = f"{text} ({fact.entity}.{fact.attribute} = {fact.value})"
        lines.append(f"- {text}")
    return "\n".join(lines)


def recall(
    query: str,
    *,
    config: MemoryConfig | None = None,
    embedding_provider: EmbeddingProvider | None = None,
) -> str:
    """What the estate already knows about ``query``, as prompt text.

    Returns ``""`` when there is no store configured, when nothing
    matches, or when anything at all goes wrong. Never raises.

    The connection is opened per call rather than pooled. That is a
    deliberate simplification while this path is new: a libpq connect on
    the cluster's own network is single-digit milliseconds against a
    context budget measured in hundreds, and one connection per request
    cannot leak state between two senders. If a measurement later shows
    connect time mattering, a pool goes here and nowhere else.
    """
    if not query or not query.strip():
        return ""
    config = config or load_config()
    if not configured(config):
        return ""
    provider = (
        embedding_provider if embedding_provider is not None else provider_from_env()
    )
    try:
        with db.connect(config) as conn:
            result = retrieval.search(conn, query, provider, config=config)
    except Exception:  # noqa: BLE001 - deliberately broad, and the reason is
        # the module docstring's contract: every failure below this line,
        # from a dead database to a schema that was never migrated, must
        # degrade to "no memory this turn" and not to a sender who never
        # gets an answer. Logged at warning so it is visible, not silent.
        _LOG.warning("memory recall failed; answering without memory", exc_info=True)
        return ""
    if not result.facts:
        return ""
    assembly = context_mod.assemble_context(result.facts, config)
    if not assembly.facts:
        return ""
    return _render(assembly.facts, result.tainted)
