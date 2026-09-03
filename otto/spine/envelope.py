"""The task envelope (spec §3) and the two structural invariants that ride
with it everywhere on the bus: the authority tier (§9) and taint (§10).

The envelope's ``task_id`` is a ULID and doubles as the OpenTelemetry trace
id (spec §3, first sentence) — one identifier, not two kept in sync by
convention. Pydantic strict mode (``model_config.strict=True``) means a
caller cannot pass ``"3"`` where ``3`` is required, or a naive datetime
where an aware one is required: the schema is the contract, not a comment
next to a dict literal.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
)
from ulid import ULID


class Tier(str, Enum):
    """Authority tier, spec §9. Ordered T0 < T1 < T2 < T3."""

    T0 = "T0"
    T1 = "T1"
    T2 = "T2"
    T3 = "T3"

    @property
    def rank(self) -> int:
        return {"T0": 0, "T1": 1, "T2": 2, "T3": 3}[self.value]

    def __lt__(self, other: "Tier") -> bool:
        return self.rank < other.rank

    def __le__(self, other: "Tier") -> bool:
        return self.rank <= other.rank


class TaskClass(str, Enum):
    """Spec §3 ``class`` field. Renamed here because ``class`` is a Python
    keyword; the wire field stays ``class`` via the pydantic alias below."""

    research = "research"
    code = "code"
    ops_read = "ops_read"
    comms = "comms"
    schedule = "schedule"
    memory = "memory"


class TaskSource(str, Enum):
    telegram = "telegram"
    cron = "cron"
    api = "api"
    subtask = "subtask"


class TrustTag(str, Enum):
    """Taint tag, spec §10.1. Every context block Otto ever assembles
    carries one of these; the envelope carries the *set* actually present
    in the task's context so the gateway can apply the two-source rule
    (§10.2) without re-deriving it from the transcript."""

    chidi = "chidi"
    system = "system"
    tool_trusted = "tool_trusted"
    untrusted = "untrusted"


# Tiers the two-source rule (§10.2) is permitted to cap down to. A task
# whose context holds any `untrusted` block has T2/T3 proposals queued as
# approval cards and its *effective* ceiling read as T1 for the rest of the
# task — regardless of what `authority_ceiling` says. This is enforced here
# (one place, `effective_tier`) so the gateway, the audit reader and the
# replay CLI compute the identical number from the identical envelope.
_TAINT_CAP = Tier.T1


class TaskEnvelope(BaseModel):
    """Spec §3's JSON envelope, Pydantic strict, immutable once constructed.

    `strict=True` refuses type coercion (a JSON `"600"` is not an int
    `600`); `frozen=True` means a step that wants a different ceiling makes
    a new envelope rather than mutating one in place — the escalation rule
    in §9 ("only Chidi can raise it, which itself creates a new task") is
    unenforceable if the object it names can just be edited.
    """

    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", populate_by_name=True
    )

    task_id: str
    # Which customer this task belongs to. Required, minimum length 1, no
    # default: Otto is a multi-tenant product, and a task that cannot name
    # its tenant must not be routable, billable or auditable. Every stream
    # record, every span and every log line carries it from here.
    tenant_id: str = Field(min_length=1)
    source: TaskSource
    parent_task_id: str | None = None
    task_class: TaskClass = Field(alias="class")
    input: str = Field(min_length=1)
    authority_ceiling: Tier
    context_budget_tokens: int = Field(gt=0)
    cost_budget_usd: float = Field(gt=0)
    deadline_s: int = Field(gt=0)
    created_at: AwareDatetime

    # Additions this task asked for explicitly, beyond the spec's literal
    # JSON shape: provenance (who/what actually produced this task — a
    # Telegram message id, a cron job name, the parent ULID for a subtask)
    # and the taint set the two-source rule reads.
    provenance: str = Field(min_length=1)
    taint: frozenset[TrustTag] = Field(default_factory=frozenset)

    @field_serializer("taint")
    def _serialize_taint(self, taint: frozenset[TrustTag]) -> list[str]:
        # A frozenset has no stable iteration order; a set of tags always
        # sorts the same way so canonical_json() is byte-identical for two
        # envelopes that carry the same tags in a different construction order.
        return sorted(t.value for t in taint)

    @field_validator("task_id", "parent_task_id")
    @classmethod
    def _valid_ulid(cls, v: str | None) -> str | None:
        if v is None:
            return v
        try:
            ULID.from_str(v)
        except ValueError as exc:
            raise ValueError(f"not a ULID: {v!r}: {exc}") from exc
        return v

    @property
    def effective_tier(self) -> Tier:
        """The tier the gateway is actually allowed to honour right now —
        spec §10.2, the two-source rule. `authority_ceiling` is what the
        task was *submitted* with; this is what it may *act* with."""
        if TrustTag.untrusted in self.taint and self.authority_ceiling > _TAINT_CAP:
            return _TAINT_CAP
        return self.authority_ceiling

    @property
    def is_taint_capped(self) -> bool:
        return self.effective_tier < self.authority_ceiling

    @classmethod
    def new(
        cls,
        *,
        tenant_id: str,
        source: TaskSource,
        task_class: TaskClass,
        input: str,
        authority_ceiling: Tier,
        provenance: str,
        parent_task_id: str | None = None,
        context_budget_tokens: int = 24_000,
        cost_budget_usd: float = 0.50,
        deadline_s: int = 600,
        taint: frozenset[TrustTag] = frozenset(),
        created_at: datetime | None = None,
    ) -> "TaskEnvelope":
        """Mint a fresh envelope with a fresh ULID. The one place a task_id
        is ever generated — every other constructor call takes one in."""
        return cls(
            task_id=str(ULID()),
            tenant_id=tenant_id,
            source=source,
            parent_task_id=parent_task_id,
            **{"class": task_class},
            input=input,
            authority_ceiling=authority_ceiling,
            context_budget_tokens=context_budget_tokens,
            cost_budget_usd=cost_budget_usd,
            deadline_s=deadline_s,
            created_at=created_at or datetime.now(timezone.utc),
            provenance=provenance,
            taint=frozenset(taint),
        )

    def canonical_json(self) -> bytes:
        """Deterministic wire form: sorted keys, no whitespace ambiguity.
        This is what replay hashes (`otto/spine/replay.py`) — two envelopes
        that mean the same thing produce the same bytes. `model_dump_json`
        has no `sort_keys` of its own (unlike stdlib `json.dumps`), so the
        sort happens on the dumped dict before re-encoding.
        """
        import json

        data = self.model_dump(mode="json", by_alias=True)
        return json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
