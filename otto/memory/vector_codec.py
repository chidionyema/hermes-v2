"""Text-format codec for pgvector's ``vector`` column type.

psycopg has no built-in adapter for ``vector``; both ``store.py`` (writes)
and ``retrieval.py`` (reads) need the same round trip, so it lives once,
here, instead of being duplicated or reached into as a private import.
"""

from __future__ import annotations


def embedding_to_literal(embedding: list[float] | None) -> str | None:
    """Python list -> pgvector's text input format, e.g. ``[1.0,2.0]``."""
    if embedding is None:
        return None
    return "[" + ",".join(repr(float(x)) for x in embedding) + "]"


def literal_to_embedding(value) -> list[float] | None:
    """The inverse: a fetched ``vector`` column comes back as its text
    form (psycopg has no parser registered for the type); parse it into
    a plain list of floats."""
    if value is None:
        return None
    if isinstance(value, list):
        return value
    return [float(x) for x in value.strip("[]").split(",") if x]
