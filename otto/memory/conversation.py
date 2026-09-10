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

#: The most recent answered turns for one principal on one surface, newest
#: first; the caller reverses them into reading order. Only rows that carry
#: both halves of an exchange are eligible -- a question whose answer never
#: landed is not a turn the model can learn anything from.
_RECENT = """
SELECT asked, answered
FROM otto_turns
WHERE tenant_id = %(tenant_id)s
  AND surface = %(surface)s
  AND answered IS NOT NULL
  AND answered <> ''
  AND asked_at > now() - %(within)s::interval
ORDER BY asked_at DESC
LIMIT %(limit)s
"""

#: How much of the conversation travels with the next question. Twelve
#: exchanges is a real conversation and still a small prompt; the character
#: budget is the hard stop, because one pasted document in a single turn
#: would otherwise crowd out the eleven turns around it.
DEFAULT_HISTORY_TURNS = 12
DEFAULT_HISTORY_CHARS = 24_000
DEFAULT_HISTORY_WINDOW = "12 hours"


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


def recent_messages(
    tenant_id: str,
    surface: str,
    *,
    limit: int = DEFAULT_HISTORY_TURNS,
    within: str = DEFAULT_HISTORY_WINDOW,
    max_chars: int = DEFAULT_HISTORY_CHARS,
    config: MemoryConfig | None = None,
) -> list[dict]:
    """The conversation so far, as OpenAI ``messages``, oldest first.

    Until this existed the answering lane sent the model exactly one
    message -- the current one -- so Otto could not see anything the
    founder had said a minute earlier. He sent a URL and asked for a
    summary and was told "URL not provided"; he wrote "check previous
    messages" and got a recital of stored facts back, because the fact
    store was the only past Otto had. The rows were being written the
    whole time (``record`` above, since 2026-09-08); nothing read them.

    Same two contracts as ``record``: it never raises, and it is best
    effort. A database that cannot be reached returns an empty list and
    the lane answers exactly as it did before, one message and no past.

    The budget is applied from the newest turn backwards, so a long
    conversation loses its oldest exchanges rather than its most recent
    ones.
    """
    if not fast_recall.configured(config):
        return []
    try:
        with db.connect(config) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    _RECENT,
                    {
                        "tenant_id": tenant_id,
                        "surface": surface,
                        "within": within,
                        "limit": max(0, int(limit)),
                    },
                )
                rows = cur.fetchall()
    except Exception:  # noqa: BLE001 - see the docstring: history is best
        # effort, and a store that cannot be read never costs the sender
        # their answer.
        _LOG.warning("conversation history read failed", exc_info=True)
        return []

    messages: list[dict] = []
    spent = 0
    for asked, answered in rows:  # newest first
        pair = [
            {"role": "user", "content": asked or ""},
            {"role": "assistant", "content": answered or ""},
        ]
        cost = len(pair[0]["content"]) + len(pair[1]["content"])
        if spent + cost > max_chars and messages:
            break
        spent += cost
        # Prepend: rows arrive newest first, the model reads oldest first.
        messages[:0] = pair
    return messages
