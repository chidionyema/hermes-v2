"""The neutral surface envelope (spec bullets 1 and 3).

Every surface binding produces exactly this shape, whatever the native
event looked like. Two fields carry the platform's trust model on day 0,
before any producer of them other than Telegram exists:

* ``trust_class`` — ``OPERATOR`` (the founder's authenticated channel),
  ``UNTRUSTED`` (anything else) or ``AMBIENT`` (sensor-derived: camera,
  mic — observations, never instructions). Carrying the unused
  ``AMBIENT`` value now is free; retrofitting it once a camera or mic
  producer exists is a platform rework (spec bullet 3).
* ``is_instruction_bearing`` — ``False`` for every ``AMBIENT`` envelope,
  by construction, regardless of what ``content`` says. This is the one
  place that decision is made. A gateway or router reads this property
  rather than re-deriving "is this ambient data actually an instruction"
  from the text, because that second question has no safe answer — the
  safe answer is "ambient never instructs," full stop.

A third field carries the business model, added by the founder's
2026-09-03 directive: ``tenant_id``. Otto is an enterprise, multi-tenant,
multi-channel product, so the customer a message belongs to is part of
the envelope from the first line of code — never inferred later from
which pod received it, and never carried in a deployment's environment
variables. It is required with no default: an envelope that cannot say
whose message this is has no safe reading, so it is refused at
construction rather than defaulted to some house tenant.

``Capability`` lives here (not in ``adapter.py``, which the spec's own
list associates it with) because ``SurfaceEnvelope.capabilities`` is
typed against it and ``adapter.py`` needs ``SurfaceEnvelope`` for its
``normalize`` return type — defining it in both modules would fork the
enum, and importing it from ``adapter`` into ``envelope`` would make the
two modules import each other. ``adapter.py`` re-exports the same enum
object, so ``otto.surface.adapter.Capability`` and
``otto.surface.envelope.Capability`` are the identical type.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from ulid import ULID


class TrustClass(str, Enum):
    """Spec bullet 3's three day-0 trust classes."""

    OPERATOR = "operator"
    UNTRUSTED = "untrusted"
    AMBIENT = "ambient"


class Capability(str, Enum):
    """What a surface can carry, declared by its adapter (spec bullet 2)."""

    TEXT = "text"
    RICH = "rich"
    VOICE_IN = "voice_in"
    VOICE_OUT = "voice_out"
    IMAGE_IN = "image_in"
    IMAGE_OUT = "image_out"
    APPROVAL_GESTURE = "approval_gesture"


def _new_correlation_id() -> str:
    return str(ULID())


@dataclass(frozen=True, slots=True)
class SurfaceEnvelope:
    """The one envelope shape every surface binding produces.

    ``correlation_id`` is a ULID string, minted once per inbound event
    unless the caller supplies one (a binding that already has an
    upstream correlation id — e.g. a Telegram update id folded into a
    ULID — passes it in rather than getting a second, disconnected one).
    """

    tenant_id: str
    surface: str
    principal: str | None
    trust_class: TrustClass
    capabilities: frozenset[Capability]
    content: str
    received_at: datetime
    correlation_id: str = field(default_factory=_new_correlation_id)

    def __post_init__(self) -> None:
        if not isinstance(self.tenant_id, str) or not self.tenant_id.strip():
            raise ValueError(
                "SurfaceEnvelope.tenant_id must be a non-empty string; an "
                "envelope that cannot say which customer it belongs to is "
                f"refused (got {self.tenant_id!r})"
            )
        if not self.surface:
            raise ValueError("SurfaceEnvelope.surface must be non-empty")
        if self.received_at.tzinfo is None:
            raise ValueError(
                "SurfaceEnvelope.received_at must be timezone-aware "
                f"(got naive {self.received_at!r})"
            )
        try:
            ULID.from_str(self.correlation_id)
        except ValueError as exc:
            raise ValueError(
                f"SurfaceEnvelope.correlation_id is not a ULID: {self.correlation_id!r}"
            ) from exc
        # Defensive: a caller handing in a list/set still ends up with the
        # immutable, hashable type the dataclass is typed as.
        object.__setattr__(self, "capabilities", frozenset(self.capabilities))
        # trust_class must be one of the three known classes at the
        # boundary; a raw string or unknown value is refused here rather
        # than compared against later.
        object.__setattr__(self, "trust_class", TrustClass(self.trust_class))

    @property
    def is_instruction_bearing(self) -> bool:
        """``False`` for every ``AMBIENT`` envelope, unconditionally.

        This is the taint-cap backstop the acceptance criteria name
        directly: "An ``ambient``-classed input can never carry an
        instruction that reaches a tool call." The property does not
        inspect ``content`` at all for the ``AMBIENT`` case — there is no
        content pattern that promotes ambient data to an instruction,
        because the promotion is exactly the vulnerability this rule
        closes.

        The decision is made on the value read HERE, not on what
        ``__post_init__`` saw: ``object.__setattr__`` rewrites even a
        frozen, slotted dataclass field on CPython, so construction-time
        validation alone does not bind. The value read at check time must
        be one of the three known trust classes; anything else raises
        ``ValueError`` — an unrecognised trust class is never treated as
        instruction-bearing.
        """
        try:
            trust = TrustClass(self.trust_class)
        except ValueError:
            raise ValueError(
                f"unknown trust class {self.trust_class!r}; refusing to "
                "grade instruction-bearing (known classes: "
                f"{[t.value for t in TrustClass]})"
            ) from None
        return trust is not TrustClass.AMBIENT
