"""The ``SurfaceAdapter`` protocol (spec bullet 1): the socket every later
surface — web, Slack, email, a voice session, a glasses card — plugs into
without a gateway rework. Inbound, a native event normalizes into a
``SurfaceEnvelope``; outbound, a router response renders per-surface
through a capability-negotiated ``RenderedMessage``.

``Capability`` is defined in ``otto/surface/envelope.py`` (see that
module's docstring for why) and re-exported here so callers can write
``from otto.surface.adapter import Capability`` as the spec's own module
list implies.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from otto.surface.envelope import Capability, SurfaceEnvelope

__all__ = ["Capability", "RenderedMessage", "SurfaceAdapter"]


@dataclass(frozen=True)
class RenderedMessage:
    """What ``SurfaceAdapter.render`` returns: the text a surface actually
    sends, plus a record of what happened to get there.

    ``degraded`` and ``degradation_notes`` exist so a caller — a test, an
    audit log, an operator dashboard — can tell "this reply is complete"
    from "this reply is missing something the surface could not carry"
    without re-parsing ``text`` to guess. Silent degradation (a dropped
    part with no trace of it anywhere) is exactly what this record makes
    impossible: if a part was dropped, ``degraded`` is ``True`` and its
    note is in ``degradation_notes``.
    """

    text: str
    capabilities_used: frozenset[Capability]
    degraded: bool = False
    degradation_notes: tuple[str, ...] = ()


class SurfaceAdapter(Protocol):
    """A concrete binding (``otto/surface/bindings/telegram.py``,
    ``otto/surface/bindings/http.py``, and every later surface) implements
    this shape. Nothing in ``otto/surface`` calls a concrete adapter by
    name — the gateway and router (peer lanes) depend on this protocol,
    never on ``TelegramBinding`` or ``HttpBinding`` directly.
    """

    def normalize(self, native_event: Any) -> SurfaceEnvelope:
        """Turn one native inbound event into the neutral envelope."""
        ...

    def render(
        self, response: dict[str, Any], capabilities: frozenset[Capability]
    ) -> RenderedMessage:
        """Turn one router response into what this surface can send,
        degrading explicitly (never silently) for any part the surface's
        declared ``capabilities`` cannot carry."""
        ...
