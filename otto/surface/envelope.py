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


@dataclass(frozen=True)
class SurfaceEnvelope:
    """The one envelope shape every surface binding produces.

    ``correlation_id`` is a ULID string, minted once per inbound event
    unless the caller supplies one (a binding that already has an
    upstream correlation id — e.g. a Telegram update id folded into a
    ULID — passes it in rather than getting a second, disconnected one).
    """

    surface: str
    principal: str | None
    trust_class: TrustClass
    capabilities: frozenset[Capability]
    content: str
    received_at: datetime
    correlation_id: str = field(default_factory=_new_correlation_id)

    def __post_init__(self) -> None:
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
        """
        return self.trust_class is not TrustClass.AMBIENT
