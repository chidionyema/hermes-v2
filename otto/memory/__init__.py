"""CP4 memory / context-engine core (crew#768).

Provides:
- ``models``: the Fact and Provenance shapes, fail-closed at construction.
- ``config``: configurable limits, read from env with defaults (never
  hardcoded constants).
- ``db``: a Postgres+pgvector connection resolved from env only (LAW 46),
  and an idempotent plain-SQL migration runner.
- ``embeddings``: a pluggable, provider-agnostic embedding interface
  (LAW 34) plus test doubles. No vendor SDK is imported here.
- ``retrieval``: hybrid vector+lexical search with automatic fallback to
  Postgres full-text search, and taint propagation across a result set.
- ``hygiene``: TTL expiry and duplicate compaction, with every deletion
  audited through a pluggable emitter.
- ``audit``: the pluggable audit-emitter interface and a Postgres-backed
  default so a deletion is never a black box.
"""
