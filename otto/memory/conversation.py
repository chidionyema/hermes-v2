"""The durable record of a conversation.

On 2026-09-08 the otto-gateway pod restarted at 09:22 and the founder's
08:52 exchange with Otto went with it: the question, the reply and every
lane decision behind it had lived only in that pod's stdout. It was
recovered from a recorder running outside the cluster, by luck. A product
sold to enterprise customers does not keep its conversations in a
container's memory.

This module writes one row per answered task into the estate's own
Postgres -- the same store ``otto.memory.fast_recall`` reads facts from,
reached through the same env-only connection (LAW 46). It is deliberately
NOT a second store: one database, one migration chain, one backup.

Two contracts hold here, and both are load-bearing:

* **It never raises.** A conversation record that costs a sender their
  answer is worse than no record. Every failure is logged and swallowed,
  exactly as ``_store_fact`` does for the fact tier.
* **It is one write, on the answering path.** The question and the reply
  are both in scope at exactly one point in ``otto.boot.pipeline``, so
  there is no second writer that can fall out of step with the first.

This is the ledger, and it is deliberately not the model's context. What
Otto is shown of the conversation he is having comes from the thread store
(``otto.ingress.thread`` over ``otto.memory.thread_store``), which is keyed
by the principal alone, budgeted in tokens and expired on idle. A
``recent_messages`` read over this table served that purpose for two days
and was wrong on all three counts -- it keyed on (tenant, surface), so one
person on two doors was two strangers -- and it is gone rather than left
here for the next session to wire back in.

What it does not hold: the model's intermediate reasoning, tool
arguments, and the internal router calls. Those are traces, they already
reach the estate's collector, and duplicating them here would make this
table something an operator has to redact before showing anyone. This is
the human-readable conversation and the attribution needed to explain a
bad answer: which lane, which model, how many attempts, and whether the
verify lane ever cleared it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from otto.memory import db, fast_recall
from otto.memory.config import MemoryConfig

_LOG = logging.getLogger(__name__)

_INSERT = """
INSERT INTO otto_turns
    (task_ulid, tenant_id, surface, asked_at, asked, answered,
     lane, model, attempts, outcome_state, verified,
     claims_total, claims_clean, taint_capped, cost_usd)
VALUES
    (%(task_ulid)s, %(tenant_id)s, %(surface)s, %(asked_at)s, %(asked)s,
     %(answered)s, %(lane)s, %(model)s, %(attempts)s, %(outcome_state)s,
     %(verified)s, %(claims_total)s, %(claims_clean)s, %(taint_capped)s,
     %(cost_usd)s)
ON CONFLICT (task_ulid) DO NOTHING
"""


@dataclass(frozen=True)
class Turn:
    """One answered task, as a person would read it back.

    ``verified`` is three-valued on purpose. ``None`` means the verify lane
    never ran -- it is off, out of budget, timed out, or the task was
    refused before it got there. ``False`` means it ran and did not clear
    every statement. Collapsing those two into one boolean would make the
    unverified-answer query (crew#920 item 6) unable to tell "we checked
    and it was thin" from "we never checked", which is the whole question.
    """

    task_ulid: str
    tenant_id: str
    surface: str
    asked_at: datetime
    asked: str
    answered: str | None
    lane: str | None = None
    model: str | None = None
    attempts: int | None = None
    outcome_state: str | None = None
    verified: bool | None = None
    claims_total: int | None = None
    claims_clean: int | None = None
    taint_capped: bool = False
    cost_usd: float | None = None

    def to_row(self) -> dict:
        """The insert's named parameters. One place, so the dataclass and
        the SQL cannot drift into disagreeing about a column."""
        return {
            "task_ulid": self.task_ulid,
            "tenant_id": self.tenant_id,
            "surface": self.surface,
            "asked_at": self.asked_at,
            "asked": self.asked,
            "answered": self.answered,
            "lane": self.lane,
            "model": self.model,
            "attempts": self.attempts,
            "outcome_state": self.outcome_state,
            "verified": self.verified,
            "claims_total": self.claims_total,
            "claims_clean": self.claims_clean,
            "taint_capped": self.taint_capped,
            "cost_usd": self.cost_usd,
        }


def write_turn(conn, turn: Turn) -> None:
    """Insert one turn. Raises whatever the driver raises.

    ``record()`` is the best-effort wrapper the answering path calls; this
    is the strict one, so a test can prove the row actually lands and a
    caller that genuinely wants the error can have it.

    The insert is ``ON CONFLICT DO NOTHING`` on the task ULID: a task
    replayed off the bus (``otto.ingress.worker`` redelivering after a
    restart -- exactly the situation this table exists because of) records
    its conversation once, not twice.
    """
    with conn.cursor() as cur:
        cur.execute(_INSERT, turn.to_row())
    conn.commit()


def record(turn: Turn, config: MemoryConfig | None = None) -> bool:
    """Write one turn, best effort. Returns whether the row landed.

    Never raises: an unreachable or unmigrated database loses the record
    and must not lose the sender their answer. Returns False without
    touching the network when no store is configured at all, which is the
    laptop and the test suite.
    """
    if not fast_recall.configured(config):
        return False
    try:
        with db.connect(config) as conn:
            write_turn(conn, turn)
    except Exception:  # noqa: BLE001 - see the docstring: the record is best
        # effort on the answering path and its failure is never the
        # sender's problem.
        _LOG.warning("conversation record write failed", exc_info=True)
        return False
    return True
