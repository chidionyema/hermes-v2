-- A conversation that outlives the pod that held it.
--
-- On 2026-09-08 the founder asked Otto a question at 08:52 and the whole
-- exchange -- his words, the recall, the router's lane and attempts, the
-- verify verdict, the reply that went back -- existed in exactly one place:
-- the gateway pod's stdout. The pod restarted at 09:22 and the morning was
-- gone. It survived only because a recorder happened to be running outside
-- the cluster. That is not a record; that is a coincidence.
--
-- otto_turns is the record. One row per answered task, written on the
-- answering path where the question and the reply are both in scope, so
-- there is no second write to get out of step with the first.
--
-- What it deliberately does NOT hold: the model's intermediate reasoning,
-- the tool arguments, and every internal router call. Those are traces and
-- they belong in the estate's collector, which already has them. This table
-- is the human-readable conversation -- what was asked and what was said
-- back -- which is the thing a customer asks for, a support engineer reads,
-- and an auditor subpoenas.

CREATE TABLE IF NOT EXISTS otto_turns (
  task_ulid       TEXT PRIMARY KEY,
  tenant_id       TEXT NOT NULL,
  -- Which door the message came through (telegram, slack, api ...). Kept as
  -- provenance only: nothing reads this to decide behaviour.
  surface         TEXT NOT NULL,
  asked_at        TIMESTAMPTZ NOT NULL,
  answered_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

  -- The two sides of the conversation, as a person would read them.
  asked           TEXT NOT NULL,
  answered        TEXT,

  -- How the answer was reached, so a bad reply can be attributed without
  -- re-running anything: which lane served it, how many attempts the router
  -- needed, what state it finished in, and what the verify lane made of it.
  lane            TEXT,
  model           TEXT,
  attempts        INTEGER,
  outcome_state   TEXT,
  -- NULL means the verify lane never ran (off, out of budget, timed out, or
  -- the task was refused before it could). FALSE means it ran and did not
  -- clear every statement. A reply that went out unverified is findable by
  -- this column alone, which is the point of storing it.
  verified        BOOLEAN,
  claims_total    INTEGER,
  claims_clean    INTEGER,

  -- Whether the sender was trusted, carried across from the envelope, so a
  -- row can be read without joining anything to know if it came from a
  -- tainted surface.
  taint_capped    BOOLEAN NOT NULL DEFAULT FALSE,
  cost_usd        NUMERIC(12, 6)
);

-- The two questions anyone actually asks of this table: "show me this
-- tenant's conversation, newest first" and "show me everything that went out
-- unverified".
CREATE INDEX IF NOT EXISTS otto_turns_tenant_time
  ON otto_turns (tenant_id, answered_at DESC);
CREATE INDEX IF NOT EXISTS otto_turns_unverified
  ON otto_turns (answered_at DESC) WHERE verified IS NOT TRUE;
