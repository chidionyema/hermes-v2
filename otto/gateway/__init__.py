"""CP2 tool-gateway core (crew#768).

An in-process library implementing the constitution invariants that govern
tool execution (spec `docs/founder/2026-08-31-otto-platform-build-spec-v1.md`,
sections 6, 9, 10):

- P2 — capabilities are broad, authority is tiered, enforced deterministically
  at the gateway, never by the model choosing to comply.
- P5 — untrusted content is data: any envelope carrying untrusted-content
  taint is capped at tier T1, no matter what tier it claims.
- P7 — human gate on irreversible (T3) actions: absent an approval token,
  the call is refused. Fail closed.
- "OTTO NEEDS TOTAL COVERAGE" (founder) — every call, allowed or refused,
  emits a structured audit event through a pluggable emitter.

Transport (JetStream publish, real sandbox execution) is out of scope for
this checkpoint; this module is the in-process core the transport wraps.
"""

from otto.gateway.audit import AuditEvent, AuditEmitter, InMemoryAuditEmitter
from otto.gateway.config import GatewayConfig
from otto.gateway.core import (
    ApprovalToken,
    Envelope,
    GatewayResponse,
    HumanGate,
    ToolGateway,
)
from otto.gateway.denial import Denial, DenialReason
from otto.gateway.registry import Tier, ToolRegistry, ToolSpec
from otto.gateway.errors import DuplicateTool, SchemaViolation, ToolCapacityExceeded

__all__ = [
    "ApprovalToken",
    "AuditEmitter",
    "AuditEvent",
    "Denial",
    "DenialReason",
    "DuplicateTool",
    "Envelope",
    "GatewayConfig",
    "GatewayResponse",
    "HumanGate",
    "InMemoryAuditEmitter",
    "SchemaViolation",
    "Tier",
    "ToolCapacityExceeded",
    "ToolGateway",
    "ToolRegistry",
    "ToolSpec",
]


def boot(config=None):
    """W2 wiring (crew#768): this package's boot entrypoint.

    Instruments the component through ``otto.obs`` and returns the
    handle, or raises ``ObsBootError`` — nothing boots dark (LAW 50).
    The exporter endpoint comes only from ``OTEL_EXPORTER_OTLP_ENDPOINT``;
    ``OTTO_OBS_MODE=test`` binds in-memory exporters for suites.
    """
    from otto.obs import instrument

    return instrument("gateway", config)
