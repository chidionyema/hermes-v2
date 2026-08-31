@cp4
Feature: CP4 memory engine core (crew#768)
  The memory-engine core built in this lane: a fact model that refuses a
  write without provenance, hybrid vector/lexical retrieval with automatic
  fallback, a hygiene job that expires and compacts with an audited
  deletion, and taint propagation across a retrieval result.

  This is a subset of docs/specs/otto-platform-v1/features/cp4_memory_context_engine.feature,
  scoped to the "memory engine core" build items (crew#768 task list 1-5).
  Retrieval precision (the eval suite), Telegram delivery of hygiene
  cards, context-window compaction into episodes, and subtask context
  isolation belong to the evals/gateway/spine lanes and are out of scope
  here; the hygiene scenario below is adapted to assert on this lane's
  own pluggable audit emitter instead of a Telegram chat.

  Background:
    Given a disposable Postgres instance with pgvector, migrated fresh

  Scenario: Happy path - a provenanced fact is written and retrieved
    Given a fact with entity, attribute, value and a source envelope ULID as provenance
    When mem_write commits it
    And mem_search is later called for that entity
    Then the fact is returned within the top 8 fused results

  Scenario: Edge case - a fact with no provenance is rejected, not silently dropped
    When an insert into facts is attempted with provenance NULL
    Then the database constraint rejects the insert with a non-zero exit
    And "select count(*) from facts where provenance is null" against the live table returns 0

  Scenario: Hygiene job surfaces stale and duplicate facts
    Given a fact past its stale_after date and a duplicate pair with no supersession
    When the hygiene job runs in dry-run mode
    Then the hygiene report names the stale fact and the older duplicate without deleting either
    When the hygiene job runs for real
    Then the stale fact and the older duplicate are gone
    And an audit record is emitted for each deletion

  Scenario: Network failure - Postgres connection drops mid write
    Given a mem_write in progress when the Postgres connection is dropped
    Then no partial fact row is persisted
    And the write is surfaced as a failed tool call, never a half-written fact

  Scenario: Bandwidth degradation - the hosted embedding API is unreachable or slow
    Given the hosted embedding API is unreachable or slower than the deadline
    When mem_search is called within the task's deadline_s
    Then it falls back to Postgres full-text search alone
    And it returns within deadline_s rather than hanging on the degraded dependency

  Scenario: Taint propagation - a tainted fact taints the whole result
    Given a provenanced fact captured from untrusted content
    When mem_search returns a result that includes that fact
    Then the whole retrieval result is marked tainted
