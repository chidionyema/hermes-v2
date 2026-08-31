"""The hygiene job: expires facts past their TTL and compacts duplicate
facts for the same (entity, attribute), keeping the most recent.

Two independent, fail-closed guards, belt-and-braces the same way
provenance is enforced at both the Python and SQL layers:

1. ``MemoryConfig.__post_init__`` refuses a TTL of zero or negative before
   a run can even start (a bad config can never reach this job).
2. Even with a sane TTL, this job refuses to delete more than
   ``config.hygiene_max_deletion_fraction`` of the live table in one run
   - a clock bug, a dedup key collision, or any other cause of an
   unexpectedly large candidate set stops the run, deletes nothing, and
   raises ``otto_hygiene_alerts`` loudly (queryable, and logged) rather
   than trusting the TTL/dedup logic alone to always be right.

Every deletion is audited in the SAME transaction as the delete (the
outbox pattern): the audit row is written and committed atomically with
the DELETE, so a failing pluggable notifier called afterwards can never
leave a deletion unaudited - by the time it is called, the audit row is
already durable.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row

from otto.memory.audit import AuditEmitter, AuditEvent, NullAuditEmitter
from otto.memory.config import MemoryConfig, load_config

logger = logging.getLogger(__name__)


@dataclass
class HygieneReport:
    dry_run: bool
    expired_fact_ids: list[str] = field(default_factory=list)
    compacted_fact_ids: list[str] = field(default_factory=list)
    capped: bool = False
    cap_reason: str | None = None

    @property
    def total_deleted(self) -> int:
        return len(self.expired_fact_ids) + len(self.compacted_fact_ids)


def _find_expired(
    conn: psycopg.Connection, ttl_days: int, batch_size: int, now: datetime
) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id, content
            FROM otto_facts
            WHERE
                (stale_after IS NOT NULL AND stale_after <= %(now)s)
                OR (stale_after IS NULL AND created_at <= %(now)s - (%(ttl_days)s || ' days')::interval)
            ORDER BY created_at ASC
            LIMIT %(batch_size)s
            """,
            {"now": now, "ttl_days": ttl_days, "batch_size": batch_size},
        )
        return cur.fetchall()


def _find_duplicate_groups(
    conn: psycopg.Connection, lookback_days: int, batch_size: int, now: datetime
) -> list[list[dict]]:
    """Facts sharing (entity, attribute), newest first within each group.
    Only entity/attribute-bearing facts participate - free-text facts
    with no structured key have nothing to deduplicate against."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id, content, entity, attribute, created_at
            FROM otto_facts
            WHERE entity IS NOT NULL
              AND attribute IS NOT NULL
              AND created_at >= %(since)s - (%(lookback_days)s || ' days')::interval
            ORDER BY entity, attribute, created_at DESC
            LIMIT %(batch_size)s
            """,
            {
                "since": now,
                "lookback_days": lookback_days,
                "batch_size": batch_size,
            },
        )
        rows = cur.fetchall()

    groups: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        key = (row["entity"], row["attribute"])
        groups.setdefault(key, []).append(row)
    return [rows for rows in groups.values() if len(rows) > 1]


def _count_facts(conn: psycopg.Connection) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM otto_facts")
        (total,) = cur.fetchone()
    return total


def _raise_needs_attention(
    conn: psycopg.Connection, reason: str, detail: dict, now: datetime
) -> None:
    """Loud and queryable (LAW: an instrument nobody reads is not an
    instrument): a row in otto_hygiene_alerts, plus a logger.warning so it
    also lands wherever this process's logs go."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO otto_hygiene_alerts (id, reason, detail, raised_at)
            VALUES (%s, %s, %s, %s)
            """,
            (str(uuid.uuid4()), reason, json.dumps(detail), now),
        )
    conn.commit()
    logger.warning("hygiene run capped: %s", reason)


def _delete_and_audit(
    conn: psycopg.Connection,
    emitter: AuditEmitter,
    fact_id: str,
    action: str,
    reason: str,
    detail: dict,
    now: datetime,
) -> None:
    """DELETE the fact and INSERT its audit row in one transaction, commit
    once, and only then call the pluggable emitter. If the emitter raises,
    the DELETE and its audit row are already safely committed together -
    there is no state in which a deletion is committed with no audit
    record (the defect the independent verifier demonstrated)."""
    event = AuditEvent(
        fact_id=fact_id, action=action, reason=reason, detail=detail, performed_at=now
    )
    with conn.cursor() as cur:
        cur.execute("DELETE FROM otto_facts WHERE id = %s", (fact_id,))
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
                json.dumps(event.detail) if event.detail is not None else None,
                event.performed_at,
            ),
        )
    conn.commit()
    emitter.emit(event)


def run_hygiene(
    conn: psycopg.Connection,
    config: MemoryConfig | None = None,
    audit_emitter: AuditEmitter | None = None,
    dry_run: bool = False,
    now: datetime | None = None,
) -> HygieneReport:
    config = config or load_config()  # __post_init__ already refuses TTL<=0
    now = now or datetime.now(timezone.utc)
    emitter = audit_emitter or NullAuditEmitter()
    report = HygieneReport(dry_run=dry_run)

    expired = _find_expired(
        conn, config.default_ttl_days, config.hygiene_batch_size, now
    )
    duplicate_groups = _find_duplicate_groups(
        conn, config.dedup_lookback_days, config.hygiene_batch_size, now
    )
    dup_rows = [(group[0], dup) for group in duplicate_groups for dup in group[1:]]

    candidate_count = len(expired) + len(dup_rows)
    total = _count_facts(conn)
    if total > 0 and candidate_count > 0:
        fraction = candidate_count / total
        if fraction > config.hygiene_max_deletion_fraction:
            reason = (
                f"hygiene run would delete {candidate_count}/{total} facts "
                f"({fraction:.0%}), over the "
                f"{config.hygiene_max_deletion_fraction:.0%} cap - stopping "
                "without deleting anything"
            )
            report.capped = True
            report.cap_reason = reason
            _raise_needs_attention(
                conn,
                reason,
                {
                    "candidate_count": candidate_count,
                    "total_facts": total,
                    "fraction": fraction,
                    "cap": config.hygiene_max_deletion_fraction,
                    "dry_run": dry_run,
                },
                now,
            )
            return report

    for row in expired:
        report.expired_fact_ids.append(str(row["id"]))
        if not dry_run:
            _delete_and_audit(
                conn,
                emitter,
                fact_id=str(row["id"]),
                action="expire",
                reason="past stale_after or default TTL",
                detail={"content_preview": row["content"][:200]},
                now=now,
            )

    for keep, dup in dup_rows:
        report.compacted_fact_ids.append(str(dup["id"]))
        if not dry_run:
            _delete_and_audit(
                conn,
                emitter,
                fact_id=str(dup["id"]),
                action="compact_duplicate",
                reason=f"superseded by {keep['id']}",
                detail={
                    "entity": dup["entity"],
                    "attribute": dup["attribute"],
                    "superseded_by": str(keep["id"]),
                },
                now=now,
            )

    return report
