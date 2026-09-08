"""Context budgets and compaction (crew#768 board row: "compaction and
budgets" - named explicitly on CP4's board row, and no other Otto lane
owns them).

Two pieces:

- ``assemble_context``: bounds how much of a ranked fact set is actually
  assembled into a context, by an estimated token/size budget. Assembly
  stops the moment the next fact would exceed the budget; the result
  always says whether it was truncated and names exactly which facts
  were left out - never a silent drop.

- ``compact_over_budget``: takes the facts an ``assemble_context`` call
  left out and compacts them into one summary fact, rather than
  discarding them. A compacted fact is never destroyed: its row still
  exists, gets a ``superseded_by`` link to the summary, and the change is
  audited in the same transaction it happens in (the same outbox pattern
  hygiene.py uses). ``otto/memory/retrieval.py`` excludes superseded
  facts from active search results, so a compacted fact stops competing
  for the top-k while remaining reachable by id or via its
  ``superseded_by`` chain - queryable provenance, not the void.

This is a distinct mechanism from ``hygiene.py``'s existing
``compact_duplicate`` action, which deduplicates same-(entity,attribute)
facts outright by TTL-style deletion. That mechanism predates this file
and is unchanged by it; the two share the word "compact" but not a code
path.
"""

from __future__ import annotations

import json
import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Sequence

import psycopg

from otto.memory.audit import AuditEmitter, AuditEvent, NullAuditEmitter
from otto.memory.config import MemoryConfig, load_config
from otto.memory.models import VALID_TIERS, Fact, Provenance


def estimate_tokens(text: str, chars_per_token: float) -> int:
    """A provider-agnostic token estimate (LAW 34: no vendor tokenizer
    import in core). Deliberately conservative: rounds up, and a non-empty
    string always costs at least one token."""
    return max(1, math.ceil(len(text) / chars_per_token))


@dataclass(frozen=True)
class ContextAssembly:
    facts: list[Fact]
    truncated: bool
    used_tokens: int
    budget_tokens: int
    dropped_fact_ids: list[str]


def assemble_context(
    facts: Sequence[Fact],
    config: MemoryConfig | None = None,
    budget_tokens: int | None = None,
) -> ContextAssembly:
    """Walk ``facts`` in the given (assumed priority) order, accumulating
    an estimated token cost, and stop assembly the moment the next fact
    would exceed the budget. Every fact past that point is named in
    ``dropped_fact_ids`` and ``truncated`` is set - assembly never
    silently drops a fact and says nothing about it."""
    config = config or load_config()
    budget = config.context_budget_tokens if budget_tokens is None else budget_tokens

    included: list[Fact] = []
    used = 0
    stop = len(facts)
    for i, fact in enumerate(facts):
        cost = estimate_tokens(fact.content, config.context_chars_per_token)
        if used + cost > budget:
            stop = i
            break
        included.append(fact)
        used += cost

    dropped_ids = [f.id for f in facts[stop:]]
    return ContextAssembly(
        facts=included,
        truncated=bool(dropped_ids),
        used_tokens=used,
        budget_tokens=budget,
        dropped_fact_ids=dropped_ids,
    )


@dataclass
class CompactionReport:
    summary_fact_id: str | None
    compacted_fact_ids: list[str] = field(default_factory=list)

    @property
    def compacted(self) -> bool:
        return self.summary_fact_id is not None


def compact_over_budget(
    conn: psycopg.Connection,
    assembly: ContextAssembly,
    config: MemoryConfig | None = None,
    audit_emitter: AuditEmitter | None = None,
    now: datetime | None = None,
) -> CompactionReport:
    """Compact the facts an ``assemble_context`` call dropped for being
    over budget: summarise them into one new fact, link every compacted
    source to it via ``superseded_by`` (rows kept, never deleted), and
    audit the change in the same transaction as the change itself - only
    after that commits does the pluggable emitter get called."""
    config = config or load_config()
    now = now or datetime.now(timezone.utc)
    emitter = audit_emitter or NullAuditEmitter()

    if not assembly.dropped_fact_ids:
        return CompactionReport(summary_fact_id=None)

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, content, source_envelope_ulid, tier_at_capture, tainted
            FROM otto_facts
            WHERE id = ANY(%s::uuid[]) AND superseded_by IS NULL
            ORDER BY created_at DESC
            """,
            (assembly.dropped_fact_ids,),
        )
        rows = cur.fetchall()
    if not rows:
        # already compacted by an earlier call, or nothing to do
        return CompactionReport(summary_fact_id=None)

    # A derived summary claims no more trust than its weakest source
    # (VALID_TIERS is ordered highest-authority first, so the tier with
    # the largest index is the least trustworthy), and inherits taint the
    # same way retrieval.py propagates it across a search result: any
    # tainted source taints the summary.
    tiers = [r[3] for r in rows]
    tier = max(tiers, key=VALID_TIERS.index)
    tainted = any(r[4] for r in rows)
    newest_ulid = rows[0][2]
    excerpts = "; ".join(r[1][:80] for r in rows[:5])

    summary = Fact(
        content=f"[compacted {len(rows)} facts over the {assembly.budget_tokens}-token "
        f"context budget] {excerpts}",
        provenance=Provenance(
            source_envelope_ulid=newest_ulid, tier_at_capture=tier, taint=tainted
        ),
        created_at=now,
    )
    compacted_ids = [str(r[0]) for r in rows]
    detail = {
        "compacted_fact_ids": compacted_ids,
        "budget_tokens": assembly.budget_tokens,
    }
    event = AuditEvent(
        fact_id=summary.id,
        action="context_compact",
        reason=f"compacted {len(rows)} facts over budget",
        detail=detail,
        performed_at=now,
    )

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO otto_facts
                (id, content, source_envelope_ulid, tier_at_capture, tainted, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (summary.id, summary.content, newest_ulid, tier, tainted, now),
        )
        cur.execute(
            "UPDATE otto_facts SET superseded_by = %s WHERE id = ANY(%s::uuid[])",
            (summary.id, compacted_ids),
        )
        cur.execute(
            """
            INSERT INTO otto_fact_audit (id, fact_id, action, reason, detail, performed_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                str(uuid.uuid4()),
                event.fact_id,
                event.action,
                event.reason,
                json.dumps(event.detail),
                event.performed_at,
            ),
        )
    conn.commit()
    emitter.emit(event)

    return CompactionReport(
        summary_fact_id=summary.id, compacted_fact_ids=compacted_ids
    )
