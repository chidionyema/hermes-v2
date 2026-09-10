-- The thread Otto is actually on, keyed by the person and not by the door.
--
-- otto/ingress/thread.py has declared this store since step 8 of the
-- hands-and-senses spec, as a Protocol with a SQLite implementation beside
-- it whose docstring reads "the same SQL the Postgres store runs". That
-- Postgres store was never written, so nothing in production ever imported
-- the module: the running system answered from otto_turns instead, keyed by
-- (tenant_id, surface). The founder's Telegram conversation and his portal
-- conversation were therefore two separate amnesiacs, and neither ever
-- idled out. These are the two tables the designed store needs.
--
-- Same database and same migration chain as otto_turns, deliberately, for
-- the reason 0003 gives: one database, one chain, one backup. The two are
-- not redundant. otto_turns is the durable ledger -- one row per answered
-- task, with the lane, the model, the cost and the verify verdict, which is
-- what an auditor reads. This is the live window the model is shown: the
-- exact turns, in order, bounded by a token budget, expiring on idle.

CREATE TABLE IF NOT EXISTS otto_conversation_head (
  -- The principal, and only the principal. The spec is explicit: "one
  -- thread per principal, not per surface, so the same thread continues
  -- from Telegram to the portal to a voice session".
  principal   TEXT PRIMARY KEY,
  thread_id   TEXT NOT NULL,
  -- When this thread was opened. A head older than the idle window is not
  -- deleted; it simply stops being live, and the next message opens a new
  -- thread, so yesterday's topic never bleeds into today's.
  started_at  TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS otto_conversation_turn (
  thread_id   TEXT NOT NULL,
  principal   TEXT NOT NULL,
  -- Which door this turn came through. A column, never a key: it is here so
  -- the thread can be read back per surface for support, not so the model
  -- is shown a different past depending on where the person is standing.
  surface     TEXT NOT NULL,
  -- Already in the provider's wire shape (user / assistant / tool), so
  -- building the model's messages list is a projection and never a mapping.
  role        TEXT NOT NULL,
  content     TEXT NOT NULL,
  tool_name   TEXT NOT NULL DEFAULT '',
  created_at  TIMESTAMPTZ NOT NULL
);

-- The one query the answering path runs: this thread's turns, in order.
CREATE INDEX IF NOT EXISTS otto_conversation_turn_thread
  ON otto_conversation_turn (thread_id, created_at);
-- And the one a support engineer runs: this person's turns, newest first.
CREATE INDEX IF NOT EXISTS otto_conversation_turn_principal
  ON otto_conversation_turn (principal, created_at DESC);
