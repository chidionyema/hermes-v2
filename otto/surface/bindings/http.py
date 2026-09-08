"""The HTTP surface binding (spec bullet 5): the companion app's future
socket — a plain POST payload dict in, ``SurfaceEnvelope`` out.

Pure function — no network call, no server started here (this module
only normalizes a payload someone else received; it never listens on a
port). The principal is resolved from an allow-list the caller passes in
at construction time (LAW 46) — this module names no caller id of its
own. Produces the same envelope shape as ``bindings/telegram.py`` for
identical ``content`` and an equivalent principal mapping (spec bullet
5, acceptance bullet 1).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from otto.surface.adapter import RenderedMessage
from otto.surface.envelope import Capability, SurfaceEnvelope, TrustClass
from otto.surface.identity import BOUND_ACCOUNT, validate_principal_source
from otto.surface.renderer import parts_from_response, render_parts

HTTP_CAPABILITIES: frozenset[Capability] = frozenset({Capability.TEXT, Capability.RICH})


@dataclass(frozen=True, slots=True)
class HttpBinding:
    """``principal_allowlist`` maps an opaque caller id — already
    authenticated upstream of this function, by whatever the HTTP
    surface's own auth layer is; this binding neither performs nor
    assumes that authentication — to a bound-account principal name.
    """

    principal_allowlist: dict[str, str]

    def normalize(
        self, native_event: dict[str, Any], *, tenant_id: str
    ) -> SurfaceEnvelope:
        caller_id = native_event.get("caller_id")
        text = native_event.get("content", "")

        principal = (
            self.principal_allowlist.get(caller_id) if caller_id is not None else None
        )
        if principal is not None:
            validate_principal_source(BOUND_ACCOUNT)
            trust_class = TrustClass.OPERATOR
        else:
            trust_class = TrustClass.UNTRUSTED

        return SurfaceEnvelope(
            tenant_id=tenant_id,
            surface="http",
            principal=principal,
            trust_class=trust_class,
            capabilities=HTTP_CAPABILITIES,
            content=text,
            received_at=_parse_timestamp(native_event.get("received_at")),
        )

    def render(
        self, response: dict[str, Any], capabilities: frozenset[Capability]
    ) -> RenderedMessage:
        return render_parts(parts_from_response(response), capabilities, surface="http")


def _parse_timestamp(raw: str | None) -> datetime:
    if raw is None:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed
