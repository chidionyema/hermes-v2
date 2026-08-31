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

    # "" = use the package's own otto/memory/migrations directory
    migrations_env_dir: str = ""


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
        migrations_env_dir=_env_str("OTTO_MEMORY_MIGRATIONS_DIR", ""),
    )
