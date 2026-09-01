-- CP4 hardening pass (crew#768, independent-verifier fixes).
--
-- Applied idempotently, same convention as 0001: every statement can be
-- re-run against a database that already has it.
--
-- 1. Empty-string provenance was accepted by the schema before this file
--    (TEXT NOT NULL passes on ''); the Python layer already refused it
--    (otto/memory/models.py Provenance.__post_init__), but a caller that
--    writes raw SQL directly bypassed that. This CHECK refuses it at the
--    table itself - the same fail-closed-where-inputs-merge principle
--    0001 already applied to NULL.
--
--    The pattern matches a standard 26-character Crockford Base32 ULID
--    (0-9, A-Z minus I/L/O/U, which are excluded because they are easily
--    confused with 1/1/0/0). Every ULID this codebase generates already
--    conforms to it.
ALTER TABLE otto_facts DROP CONSTRAINT IF EXISTS otto_facts_ulid_shape;
ALTER TABLE otto_facts ADD CONSTRAINT otto_facts_ulid_shape
    CHECK (source_envelope_ulid ~ '^[0-9A-HJKMNP-TV-Z]{26}$');

-- 2. Context-budget compaction (otto/memory/context.py) writes an audit
--    action the original 0001 CHECK did not allow.
ALTER TABLE otto_fact_audit DROP CONSTRAINT IF EXISTS otto_fact_audit_action_check;
ALTER TABLE otto_fact_audit ADD CONSTRAINT otto_fact_audit_action_check
    CHECK (action IN ('expire', 'compact_duplicate', 'context_compact'));

-- 3. A hygiene run that would delete more than
--    MemoryConfig.hygiene_max_deletion_fraction of the table stops and
--    deletes nothing (otto/memory/hygiene.py); this is where it lands
--    loudly and queryably instead of only in a log line.
CREATE TABLE IF NOT EXISTS otto_hygiene_alerts (
    id UUID PRIMARY KEY,
    reason TEXT NOT NULL,
    detail JSONB,
    raised_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS otto_hygiene_alerts_raised_at_idx
    ON otto_hygiene_alerts (raised_at);
