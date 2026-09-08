"""The Fact model.

Spec (docs/founder/2026-08-31-otto-platform-build-spec-v1.md) section 8:
"Provenance is NOT NULL - a fact without a source cannot be written; the
gateway rejects it." This module is the first place that rule is
enforced: a Fact object cannot even be constructed without provenance,
and the store's SQL schema (otto/memory/migrations) enforces the same
rule again with a NOT NULL constraint at the table itself, so a caller
that bypasses this class and writes SQL directly is still refused
(fail-closed where the inputs merge, not just at one call site).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


class ProvenanceError(ValueError):
    """Raised when a fact is constructed or written without provenance."""


# Authority tiers from the platform constitution, section 9. A closed set
# rather than a free string, so a bad tier fails at construction, not at
# a query three hops later.
VALID_TIERS = ("T0", "T1", "T2", "T3")


@dataclass(frozen=True)
class Provenance:
    """Where a fact came from, and how much it can be trusted.

    - ``source_envelope_ulid``: the task/tool-call envelope ULID that
      produced this fact (spec section 3's task envelope, or a tool
      call's own id). Required and non-empty - this is the provenance.
    - ``tier_at_capture``: the authority tier in force when the fact was
      captured (T0-T3, spec section 9).
    - ``taint``: True when the fact derives, even partially, from
      untrusted content (spec section 10's trust tags: any block tagged
      other than ``chidi``/``system``/``tool_trusted``). A tainted fact
      can still be stored and retrieved, but any retrieval result that
      includes it is itself tainted (otto/memory/retrieval.py).
    """

    source_envelope_ulid: str
    tier_at_capture: str
    taint: bool = False

    def __post_init__(self) -> None:
        if not self.source_envelope_ulid or not self.source_envelope_ulid.strip():
            raise ProvenanceError(
                "provenance.source_envelope_ulid is required and cannot be empty"
            )
        if self.tier_at_capture not in VALID_TIERS:
            raise ProvenanceError(
                f"provenance.tier_at_capture must be one of {VALID_TIERS}, "
                f"got {self.tier_at_capture!r}"
            )


@dataclass(frozen=True)
class Fact:
    """A single stored fact.

    ``content`` is the human-readable text used for full-text search and,
    when an embedding provider is present, for embedding. ``entity``,
    ``attribute`` and ``value`` are optional structured fields (the
    spec's entity/attribute/value shape, section 8) kept alongside the
    free-text content; a caller may populate either or both.
    """

    content: str
    provenance: Provenance
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    entity: str | None = None
    attribute: str | None = None
    value: str | None = None
    embedding: list[float] | None = None
    confidence: float | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_verified_at: datetime | None = None
    stale_after: datetime | None = None
    superseded_by: str | None = None

    def __post_init__(self) -> None:
        if self.provenance is None:
            raise ProvenanceError("a fact without provenance is refused at write")
        if not self.content or not self.content.strip():
            raise ValueError("fact.content is required and cannot be empty")
        if self.embedding is not None and len(self.embedding) == 0:
            raise ValueError("fact.embedding, when present, cannot be empty")

    def to_row(self) -> dict[str, Any]:
        """The dict shape ``store.py`` inserts, one place both directions
        (write and the tests that assert on it) agree on."""
        return {
            "id": self.id,
            "content": self.content,
            "entity": self.entity,
            "attribute": self.attribute,
            "value": self.value,
            "embedding": self.embedding,
            "source_envelope_ulid": self.provenance.source_envelope_ulid,
            "tier_at_capture": self.provenance.tier_at_capture,
            "tainted": self.provenance.taint,
            "confidence": self.confidence,
            "created_at": self.created_at,
            "last_verified_at": self.last_verified_at,
            "stale_after": self.stale_after,
            "superseded_by": self.superseded_by,
        }

    @staticmethod
    def from_row(row: dict[str, Any]) -> Fact:
        return Fact(
            id=str(row["id"]),
            content=row["content"],
            entity=row.get("entity"),
            attribute=row.get("attribute"),
            value=row.get("value"),
            embedding=row.get("embedding"),
            provenance=Provenance(
                source_envelope_ulid=row["source_envelope_ulid"],
                tier_at_capture=row["tier_at_capture"],
                taint=bool(row["tainted"]),
            ),
            confidence=row.get("confidence"),
            created_at=row["created_at"],
            last_verified_at=row.get("last_verified_at"),
            stale_after=row.get("stale_after"),
            superseded_by=(
                str(row["superseded_by"]) if row.get("superseded_by") else None
            ),
        )
