"""The hygiene job: expires facts past their TTL and compacts duplicate
facts for the same (entity, attribute), keeping the most recent. Every
deletion - expiry or compaction - emits an audit record through a
pluggable ``AuditEmitter`` (otto/memory/audit.py) so a hygiene run is
never a black box (spec P4/P8 in spirit: what changed, and why, is
always recoverable).

Bounded per call by ``config.hygiene_batch_size`` (a configurable limit,
not a hardcoded one) so a single run cannot lock the table indefinitely;
a scheduler calls this repeatedly until it returns an empty report.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row

from otto.memory.audit import AuditEmitter, AuditEvent, PostgresAuditEmitter
from otto.memory.config import MemoryConfig, load_config


@dataclass
class HygieneReport:
    dry_run: bool
    expired_fact_ids: list[str] = field(default_factory=list)
    compacted_fact_ids: list[str] = field(default_factory=list)

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


def run_hygiene(
    conn: psycopg.Connection,
    config: MemoryConfig | None = None,
    audit_emitter: AuditEmitter | None = None,
    dry_run: bool = False,
    now: datetime | None = None,
) -> HygieneReport:
    config = config or load_config()
    now = now or datetime.now(timezone.utc)
    emitter = audit_emitter or PostgresAuditEmitter(conn)
    report = HygieneReport(dry_run=dry_run)

    expired = _find_expired(
        conn, config.default_ttl_days, config.hygiene_batch_size, now
    )
    for row in expired:
        report.expired_fact_ids.append(str(row["id"]))
        if not dry_run:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM otto_facts WHERE id = %s", (row["id"],))
            conn.commit()
            emitter.emit(
                AuditEvent(
                    fact_id=str(row["id"]),
                    action="expire",
                    reason="past stale_after or default TTL",
                    detail={"content_preview": row["content"][:200]},
                    performed_at=now,
                )
            )

    duplicate_groups = _find_duplicate_groups(
        conn, config.dedup_lookback_days, config.hygiene_batch_size, now
    )
    for group in duplicate_groups:
        keep, *older = group  # newest first (ORDER BY created_at DESC)
        for dup in older:
            report.compacted_fact_ids.append(str(dup["id"]))
            if not dry_run:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM otto_facts WHERE id = %s", (dup["id"],))
                conn.commit()
                emitter.emit(
                    AuditEvent(
                        fact_id=str(dup["id"]),
                        action="compact_duplicate",
                        reason=f"superseded by {keep['id']}",
                        detail={
                            "entity": dup["entity"],
                            "attribute": dup["attribute"],
                            "superseded_by": str(keep["id"]),
                        },
                        performed_at=now,
                    )
                )

    return report
