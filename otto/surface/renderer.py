"""Shared capability-negotiating renderer helpers (spec bullet 2).

A concrete ``SurfaceAdapter.render`` (Telegram, HTTP, and every later
surface) calls ``render_parts`` rather than re-implementing capability
negotiation per surface. The rule is one sentence, and this module is the
one place it is coded: a response part needing a capability the surface
does not declare renders as text stating the degradation — using a
configurable template — never dropped. A claim's status marker (for
example ``UNVERIFIED``) is content, not a capability-gated part, so it is
never a candidate for degradation and survives onto every surface
unchanged (acceptance bullet 2: "a response with an UNVERIFIED claim
renders the marker on BOTH surfaces").
"""

from __future__ import annotations

from dataclasses import dataclass

from otto.surface.adapter import RenderedMessage
from otto.surface.envelope import Capability

DEFAULT_DEGRADATION_TEMPLATE = "[{surface} cannot render {needed}: {summary}]"


@dataclass(frozen=True)
class ResponsePart:
    """One piece of a router response.

    ``kind`` is the capability required to render this part natively;
    ``TEXT`` is the universal fallback every surface in this contract
    declares, so a ``TEXT`` part never degrades. ``claim_status`` carries
    a marker such as ``"UNVERIFIED"`` that must appear in the rendered
    output on every surface, unconditionally — it rides with the part's
    text rather than being a capability of its own.
    """

    kind: Capability
    text: str
    claim_status: str | None = None


def render_parts(
    parts: list[ResponsePart],
    capabilities: frozenset[Capability],
    *,
    surface: str,
    template: str = DEFAULT_DEGRADATION_TEMPLATE,
) -> RenderedMessage:
    """Render ``parts`` for a surface that declares ``capabilities``.

    Every part is emitted as a line of text. A part whose ``kind`` is
    declared by ``capabilities`` (or is ``TEXT``, always assumed
    available) renders as its own text, with ``claim_status`` prefixed
    when present. A part whose ``kind`` is NOT declared renders as the
    degradation template instead — the part's text still appears, inside
    the stated-degradation line, so nothing is silently dropped; it is
    visibly downgraded to text.
    """
    lines: list[str] = []
    notes: list[str] = []
    used: set[Capability] = {Capability.TEXT}
    degraded = False

    for part in parts:
        body = f"{part.claim_status}: {part.text}" if part.claim_status else part.text
        if part.kind is Capability.TEXT or part.kind in capabilities:
            lines.append(body)
            used.add(part.kind)
        else:
            degraded = True
            note = template.format(
                surface=surface, needed=part.kind.value, summary=body
            )
            lines.append(note)
            notes.append(note)

    return RenderedMessage(
        text="\n".join(lines),
        capabilities_used=frozenset(used),
        degraded=degraded,
        degradation_notes=tuple(notes),
    )


def parts_from_response(response: dict) -> list[ResponsePart]:
    """Parse the router's wire-shaped response
    (``{"parts": [{"kind": ..., "text": ..., "claim_status": ...}, ...]}``)
    into ``ResponsePart`` objects. Every concrete binding's ``render``
    calls this before ``render_parts`` so the parsing rule lives once,
    here, instead of once per surface.
    """
    parts = []
    for raw in response.get("parts", []):
        parts.append(
            ResponsePart(
                kind=Capability(raw["kind"]),
                text=raw["text"],
                claim_status=raw.get("claim_status"),
            )
        )
    return parts
