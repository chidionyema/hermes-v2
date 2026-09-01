"""Configurable limits for the memory engine.

LAW 46: no path, host, port, account or credential is ever a literal in
code. Every knob here is an environment variable with a stated default;
nothing is a bare constant a caller cannot override.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env_str(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw not in (None, "") else default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return float(raw) if raw not in (None, "") else default


class ConfigError(ValueError):
    """A configured limit is nonsensical enough that running with it would
    silently do the wrong thing (e.g. a TTL of zero expiring every fact
    in the store). Fail closed at construction, not three calls later."""


@dataclass(frozen=True)
class MemoryConfig:
    """One place all configurable memory-engine limits are read from.

    Every field has a default so the engine runs out of the box, and every
    field is overridable from the environment so no limit is a hardcoded
    constant a caller cannot change.
    """

    # Connection: LAW 46 - env only, no default host/port/credential.
    # OTTO_MEMORY_DATABASE_URL is a libpq connection string. When unset,
    # psycopg falls back to the standard PG* libpq environment variables
    # (PGHOST, PGPORT, PGUSER, PGPASSWORD, PGDATABASE) itself - still env
    # only, never a literal in this file.
    database_url_env: str = "OTTO_MEMORY_DATABASE_URL"

    # Embedding dimension. pgvector needs a fixed dimension per column;
    # this is templated into the migration at apply time, never baked in.
    embedding_dim: int = 1536

    # Retrieval.
    retrieval_top_k: int = 8
    # How many candidates each arm (vector, lexical) contributes before
    # fusion - the spec's "merged top-40" language, made configurable.
    retrieval_candidate_pool: int = 40
    # Deadline for the embedding provider before falling back to lexical
    # full-text search alone (the cp4 degradation scenario).
    embedding_deadline_s: float = 2.0
    # Reciprocal-rank-fusion constant (standard IR default is 60).
    rrf_k: int = 60

    # Hygiene.
    default_ttl_days: int = 90
    hygiene_batch_size: int = 500
    dedup_lookback_days: int = 365
    # Circuit breaker (LAW: self-healing needs a circuit breaker, applied
    # here to a destructive job): a single hygiene run may never delete
    # more than this fraction of the live table. A bad TTL, a clock bug or
    # a dedup key collision all fail closed through this one cap - stop,
    # delete nothing, raise otto_hygiene_alerts loudly - rather than
    # trusting the TTL/dedup logic alone to be correct forever.
    hygiene_max_deletion_fraction: float = 0.2

    # Context assembly and compaction (crew#768 board row: "compaction and
    # budgets", explicitly owned by this lane).
    context_budget_tokens: int = 2000
    # Provider-agnostic token estimate (LAW 34: no vendor tokenizer import
    # in core): len(text) / chars_per_token, rounded up.
    context_chars_per_token: float = 4.0

    # "" = use the package's own otto/memory/migrations directory
    migrations_env_dir: str = ""

    def __post_init__(self) -> None:
        if self.default_ttl_days <= 0:
            raise ConfigError(
                "default_ttl_days must be > 0 (fail closed): a TTL of zero "
                f"or negative would expire every fact in the store; got "
                f"{self.default_ttl_days}"
            )
        if not (0 < self.hygiene_max_deletion_fraction <= 1):
            raise ConfigError(
                "hygiene_max_deletion_fraction must be in (0, 1]; got "
                f"{self.hygiene_max_deletion_fraction}"
            )
        if self.context_budget_tokens <= 0:
            raise ConfigError(
                f"context_budget_tokens must be > 0; got {self.context_budget_tokens}"
            )
        if self.context_chars_per_token <= 0:
            raise ConfigError(
                "context_chars_per_token must be > 0; got "
                f"{self.context_chars_per_token}"
            )


def load_config() -> MemoryConfig:
    """Re-read config from the environment. Call per-process, not once at
    import time, so tests can override env vars per-scenario."""
    return MemoryConfig(
        database_url_env=_env_str(
            "OTTO_MEMORY_DATABASE_URL_ENV_NAME", "OTTO_MEMORY_DATABASE_URL"
        ),
        embedding_dim=_env_int("OTTO_MEMORY_EMBEDDING_DIM", 1536),
        retrieval_top_k=_env_int("OTTO_MEMORY_RETRIEVAL_TOP_K", 8),
        retrieval_candidate_pool=_env_int("OTTO_MEMORY_RETRIEVAL_CANDIDATE_POOL", 40),
        embedding_deadline_s=_env_float("OTTO_MEMORY_EMBEDDING_DEADLINE_S", 2.0),
        rrf_k=_env_int("OTTO_MEMORY_RRF_K", 60),
        default_ttl_days=_env_int("OTTO_MEMORY_DEFAULT_TTL_DAYS", 90),
        hygiene_batch_size=_env_int("OTTO_MEMORY_HYGIENE_BATCH_SIZE", 500),
        dedup_lookback_days=_env_int("OTTO_MEMORY_DEDUP_LOOKBACK_DAYS", 365),
        hygiene_max_deletion_fraction=_env_float(
            "OTTO_MEMORY_HYGIENE_MAX_DELETION_FRACTION", 0.2
        ),
        context_budget_tokens=_env_int("OTTO_MEMORY_CONTEXT_BUDGET_TOKENS", 2000),
        context_chars_per_token=_env_float("OTTO_MEMORY_CONTEXT_CHARS_PER_TOKEN", 4.0),
        migrations_env_dir=_env_str("OTTO_MEMORY_MIGRATIONS_DIR", ""),
    )
