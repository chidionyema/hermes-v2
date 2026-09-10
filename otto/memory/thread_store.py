"""``ConversationStore`` on PostgreSQL: the store the design has been
waiting for.

``otto.ingress.thread`` declared ``ConversationStore`` as a Protocol and
shipped ``SqliteConversationStore`` beside it, whose docstring says "the
same SQL the Postgres store runs". Nobody ever wrote the Postgres store,
so nothing in production could import the module, so the running system
grew a second, lesser conversation path instead: ``recent_messages`` over
``otto_turns``, keyed by ``(tenant_id, surface)``. That path breaks the
design in three ways the spec names explicitly --

* it keys on the surface, so the same person on Telegram and on the portal
  is two strangers to Otto;
* it has no token budget, only a row count, so one pasted document evicts
  nothing and the prompt grows without bound;
* it never idles, so a question asked this morning is still context at
  midnight.

This module is the missing half. The SQL is the SQLite store's SQL with
``%s`` placeholders instead of ``?`` and ``now() - interval`` instead of a
Python-computed cut-off string, so the in-memory double the tests bind is
exercising the real shape.

``psycopg`` and not ``asyncpg``, for the reason ``otto.ingress.pg_store``
gives: the answering path is synchronous, and pulling an event loop into
it would buy nothing. No connection pool, for the same reason -- one short
statement per turn against a Service in the same cluster.

LAW 46: nothing here names a host, a port, a database or a password. The
connection comes from ``otto.memory.db``, which reads the environment.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from otto.ingress.thread import (
    DEFAULT_IDLE_HOURS,
    ConversationStore,
    ThreadSlice,
    Turn,
    _new_opaque_thread_id,
)
from otto.memory import db, fast_recall
from otto.memory.config import MemoryConfig

_LOG = logging.getLogger(__name__)

#: The live head for a principal: their thread, if it has not idled out.
#: The interval is passed as a parameter rather than interpolated, so an
#: idle window can never be built out of string concatenation.
_HEAD = """
SELECT thread_id
FROM otto_conversation_head
WHERE principal = %(principal)s
  AND started_at >= now() - %(idle)s::interval
"""

_TURNS = """
SELECT thread_id, principal, surface, role, content, tool_name, created_at
FROM otto_conversation_turn
WHERE thread_id = %(thread_id)s
  AND created_at >= now() - %(idle)s::interval
ORDER BY created_at
"""

_OPEN_HEAD = """
INSERT INTO otto_conversation_head (principal, thread_id, started_at)
VALUES (%(principal)s, %(thread_id)s, now())
ON CONFLICT (principal) DO UPDATE
    SET thread_id = EXCLUDED.thread_id, started_at = EXCLUDED.started_at
"""

_APPEND = """
INSERT INTO otto_conversation_turn
    (thread_id, principal, surface, role, content, tool_name, created_at)
VALUES
    (%(thread_id)s, %(principal)s, %(surface)s, %(role)s, %(content)s,
     %(tool_name)s, now())
"""


@dataclass
class PostgresConversationStore:
    """The running store. Implements ``ConversationStore`` exactly.

    ``idle_hours`` is the same knob ``SqliteConversationStore`` carries and
    defaults to the same value, so the two implementations cannot drift
    into disagreeing about when a conversation is over.
    """

    config: MemoryConfig | None = None
    idle_hours: float = DEFAULT_IDLE_HOURS

    @property
    def _idle(self) -> str:
        return f"{self.idle_hours} hours"

    def thread_for(self, principal: str) -> ThreadSlice | None:
        with db.connect(self.config) as conn:
            with conn.cursor() as cur:
                cur.execute(_HEAD, {"principal": principal, "idle": self._idle})
                head = cur.fetchone()
                if head is None:
                    return None
                cur.execute(_TURNS, {"thread_id": head[0], "idle": self._idle})
                rows = cur.fetchall()
        if not rows:
            return None
        return ThreadSlice(thread_id=head[0], turns=[_row_to_turn(r) for r in rows])

    def append(
        self,
        principal: str,
        surface: str,
        *,
        role: str,
        content: str,
        tool_name: str = "",
    ) -> str:
        with db.connect(self.config) as conn:
            with conn.cursor() as cur:
                cur.execute(_HEAD, {"principal": principal, "idle": self._idle})
                head = cur.fetchone()
                thread_id = head[0] if head is not None else _new_opaque_thread_id()
                if head is None:
                    cur.execute(
                        _OPEN_HEAD, {"principal": principal, "thread_id": thread_id}
                    )
                cur.execute(
                    _APPEND,
                    {
                        "thread_id": thread_id,
                        "principal": principal,
                        "surface": surface,
                        "role": role,
                        "content": content,
                        "tool_name": tool_name,
                    },
                )
            conn.commit()
        return thread_id

    def new_topic(self, principal: str) -> str:
        """Open a fresh thread and make it the principal's head. Nothing is
        deleted; the old thread's rows stay and simply stop being live."""
        thread_id = _new_opaque_thread_id()
        with db.connect(self.config) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    _OPEN_HEAD, {"principal": principal, "thread_id": thread_id}
                )
            conn.commit()
        return thread_id


def _row_to_turn(row) -> Turn:
    created = row[6]
    if isinstance(created, datetime) and created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return Turn(
        thread_id=row[0],
        principal=row[1],
        surface=row[2],
        role=row[3],
        content=row[4],
        tool_name=row[5] or "",
        created_at=created,
    )


@dataclass
class BestEffortConversationStore:
    """The answering path's wrapper: a thread store that can never cost a
    sender their answer.

    Same contract the memory and record tiers already hold (``fast_recall``,
    ``conversation.record``): an unreachable or unmigrated database means no
    history and no append, logged, never raised. A conversation feature that
    can turn a database blip into silence from Otto is not shippable, and
    this is the one place that rule is implemented for threads.
    """

    inner: ConversationStore
    reads: list[str] = field(default_factory=list)

    def thread_for(self, principal: str) -> ThreadSlice | None:
        try:
            return self.inner.thread_for(principal)
        except Exception:  # noqa: BLE001 - see the docstring
            _LOG.warning("thread read failed", exc_info=True)
            return None

    def append(
        self,
        principal: str,
        surface: str,
        *,
        role: str,
        content: str,
        tool_name: str = "",
    ) -> str:
        try:
            return self.inner.append(
                principal, surface, role=role, content=content, tool_name=tool_name
            )
        except Exception:  # noqa: BLE001 - see the docstring
            _LOG.warning("thread append failed", exc_info=True)
            return ""

    def new_topic(self, principal: str) -> str:
        try:
            return self.inner.new_topic(principal)
        except Exception:  # noqa: BLE001 - see the docstring
            _LOG.warning("thread new_topic failed", exc_info=True)
            return ""


def store_or_none(config: MemoryConfig | None = None) -> ConversationStore | None:
    """The running thread store, or ``None`` when no database is configured.

    ``None`` is the laptop and the test suite, and it is not an outage: the
    answering lane answers exactly as it did before threads existed, which
    is the "unconfigured is not an outage" rule ``otto.ingress.thread``
    states in its own header.
    """
    if not fast_recall.configured(config):
        return None
    return BestEffortConversationStore(PostgresConversationStore(config=config))
