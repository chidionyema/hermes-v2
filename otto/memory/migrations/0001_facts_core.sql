-- CP4 memory engine core schema (crew#768).
--
-- Reuses the estate's own pgvector pattern (idp platform/hindsight/postgres.yaml,
-- pgvector 0.8.6-pg17) instead of a new database product (LAW 43).
--
-- Applied idempotently: every statement is IF NOT EXISTS / OR REPLACE, so
-- re-running this file against a database that already has it is a no-op.
--
-- {{EMBEDDING_DIM}} is substituted by otto/memory/db.py from
-- MemoryConfig.embedding_dim before this file is executed (LAW 46: the
-- dimension is a config value, never a literal baked into a checked-in
-- migration).

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS otto_facts (
    id UUID PRIMARY KEY,
    content TEXT NOT NULL,
    entity TEXT,
    attribute TEXT,
    value TEXT,
    embedding vector({{EMBEDDING_DIM}}),
    content_tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,

    -- Provenance. NOT NULL at the table itself: this is the fail-closed
    -- point where every writer merges, regardless of which caller (or
    -- which future caller that skips otto/memory/models.py entirely and
    -- writes SQL directly) is doing the inserting.
    source_envelope_ulid TEXT NOT NULL,
    tier_at_capture TEXT NOT NULL CHECK (tier_at_capture IN ('T0', 'T1', 'T2', 'T3')),
    tainted BOOLEAN NOT NULL DEFAULT false,

    confidence REAL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_verified_at TIMESTAMPTZ,
    stale_after TIMESTAMPTZ,
    superseded_by UUID REFERENCES otto_facts(id)
);

CREATE INDEX IF NOT EXISTS otto_facts_content_tsv_idx ON otto_facts USING GIN (content_tsv);
CREATE INDEX IF NOT EXISTS otto_facts_entity_attribute_idx ON otto_facts (entity, attribute)
    WHERE entity IS NOT NULL AND attribute IS NOT NULL;
CREATE INDEX IF NOT EXISTS otto_facts_stale_after_idx ON otto_facts (stale_after)
    WHERE stale_after IS NOT NULL;
-- ivfflat needs training data to be useful; created empty here, cheap and
-- idempotent, and does no harm on a table with zero or few rows in tests.
CREATE INDEX IF NOT EXISTS otto_facts_embedding_idx ON otto_facts
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- Every hygiene-job deletion is audited here (otto/memory/audit.py's
-- default emitter), so a compaction or expiry is never a black box.
CREATE TABLE IF NOT EXISTS otto_fact_audit (
    id UUID PRIMARY KEY,
    fact_id UUID NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('expire', 'compact_duplicate')),
    reason TEXT NOT NULL,
    detail JSONB,
    performed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS otto_fact_audit_performed_at_idx ON otto_fact_audit (performed_at);
