"""The tool gateway: the single point where a call is validated, tiered,
taint-capped, human-gated, executed and audited.

Design note (estate rule: "remove the bad input, don't guard it"): the
taint cap is computed exactly once, at the top of ``ToolGateway.call``,
where the envelope's claimed ceiling and its taint set merge into one
number — ``effective_tier``. Every check after that line reads
``effective_tier`` only; none of them re-reads ``envelope.untrusted``.
There is no downstream code path that could forget to re-check taint,
because there is nothing left downstream that still trusts the unclamped
ceiling.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

import jsonschema

from otto.gateway.audit import AuditEmitter, AuditEvent, InMemoryAuditEmitter
from otto.gateway.config import GatewayConfig
from otto.gateway.denial import Denial, DenialReason
from otto.gateway.registry import Tier, ToolRegistry, ToolSpec


@dataclass(frozen=True)
class Envelope:
    """The task envelope, as far as the gateway needs it (spec section 3).

    ``untrusted`` is True the moment any context block in the task carries
    the ``untrusted`` trust tag (spec section 10, P5) — for example a
    ``web_fetch`` result. It is a single boolean here because the gateway's
    only obligation is the cap; which sources contributed the taint is an
    audit-trail concern for the orchestrator/approval-card layer, not this
    checkpoint.
    """

    task_id: str
    authority_ceiling: Tier
    untrusted: bool = False
    envelope_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "authority_ceiling", Tier.parse(self.authority_ceiling)
        )


@dataclass(frozen=True)
class ApprovalToken:
    """What the human-gate hook must hand back for a T3/irreversible call
    to proceed. Absent this, the call is refused — fail closed."""

    token: str
    approved_by: str


class HumanGate(Protocol):
    """A callable the deployment wires to its real approval flow (a
    Telegram approval card in production). Returning ``None`` refuses the
    call; there is no other way to signal "not yet approved"."""

    def __call__(self, envelope: Envelope, tool: ToolSpec) -> ApprovalToken | None: ...


@dataclass(frozen=True)
class GatewayResponse:
    """What every ``ToolGateway.call`` returns. Never raises for an
    expected refusal — the caller always gets structured data back,
    whether the call executed or was denied."""

    ok: bool
    envelope_id: str
    tool_name: str
    output: dict[str, Any] | None = None
    denial: Denial | None = None

    @property
    def denied(self) -> bool:
        return not self.ok


@dataclass
class ToolGateway:
    registry: ToolRegistry
    audit_emitter: AuditEmitter = field(default_factory=InMemoryAuditEmitter)
    human_gate: HumanGate | None = None
    config: GatewayConfig | None = None

    def __post_init__(self) -> None:
        if self.config is None:
            self.config = self.registry.config
        self._human_gate_tiers = {Tier.parse(t) for t in self.config.human_gate_tiers}
        self._taint_ceiling = Tier.parse(self.config.taint_ceiling)

    # -- the single merge point -------------------------------------------------
    def _effective_tier(self, envelope: Envelope) -> Tier:
        """Spec P5: untrusted content caps effective authority at
        ``config.taint_ceiling`` (T1 by default) no matter what the
        envelope claims. Computed once; nothing downstream reads
        ``envelope.untrusted`` again."""
        if envelope.untrusted:
            return min(envelope.authority_ceiling, self._taint_ceiling)
        return envelope.authority_ceiling

    def call(
        self, envelope: Envelope, tool_name: str, args: dict[str, Any]
    ) -> GatewayResponse:
        start = time.perf_counter()
        effective_tier = self._effective_tier(envelope)

        tool = self.registry.get(tool_name)
        if tool is None:
            return self._deny(
                envelope,
                tool_name,
                effective_tier,
                DenialReason.UNKNOWN_TOOL,
                f"no tool named {tool_name!r} is registered",
                start,
            )

        # 1. schema validation (spec section 6, gateway responsibility #1)
        try:
            jsonschema.validate(instance=args, schema=tool.input_schema)
        except jsonschema.exceptions.ValidationError as exc:
            return self._deny(
                envelope,
                tool.name,
                effective_tier,
                DenialReason.SCHEMA_INVALID,
                f"args failed schema validation: {exc.message}",
                start,
                tool.tier,
            )

        # 2. tier check against the effective (taint-capped) ceiling
        if tool.tier > effective_tier:
            reason = (
                DenialReason.TAINT_CAP
                if envelope.untrusted and tool.tier <= envelope.authority_ceiling
                else DenialReason.TIER_INSUFFICIENT
            )
            message = (
                f"tool {tool.name!r} requires tier {tool.tier.name}, envelope's "
                f"effective ceiling is {effective_tier.name} "
                f"(claimed ceiling {envelope.authority_ceiling.name}"
                f"{', capped by untrusted taint' if envelope.untrusted else ''})"
            )
            return self._deny(
                envelope, tool.name, effective_tier, reason, message, start, tool.tier
            )

        # 3. human gate for T3 / irreversible tools — fail closed
        if tool.tier in self._human_gate_tiers or tool.irreversible:
            if self.human_gate is None:
                return self._deny(
                    envelope,
                    tool.name,
                    effective_tier,
                    DenialReason.HUMAN_APPROVAL_REQUIRED,
                    f"tool {tool.name!r} needs human approval and no human-gate hook is wired",
                    start,
                    tool.tier,
                )
            token = self.human_gate(envelope, tool)
            if token is None:
                return self._deny(
                    envelope,
                    tool.name,
                    effective_tier,
                    DenialReason.HUMAN_APPROVAL_REFUSED,
                    f"tool {tool.name!r} was not approved (no token returned)",
                    start,
                    tool.tier,
                )

        # 4. execute
        output = tool.handler(args)
        duration_ms = (time.perf_counter() - start) * 1000
        self.audit_emitter.emit(
            AuditEvent(
                envelope_id=envelope.envelope_id,
                tool_name=tool.name,
                requested_tier=envelope.authority_ceiling.name,
                effective_tier=effective_tier.name,
                decision="executed",
                duration_ms=duration_ms,
            )
        )
        return GatewayResponse(
            ok=True,
            envelope_id=envelope.envelope_id,
            tool_name=tool.name,
            output=output,
        )

    def _deny(
        self,
        envelope: Envelope,
        tool_name: str,
        effective_tier: Tier,
        reason: DenialReason,
        message: str,
        start: float,
        tool_tier: Tier | None = None,
    ) -> GatewayResponse:
        duration_ms = (time.perf_counter() - start) * 1000
        denial = Denial(
            reason=reason,
            message=message,
            envelope_id=envelope.envelope_id,
            tool_name=tool_name,
            requested_tier=envelope.authority_ceiling.name,
            effective_tier=effective_tier.name,
        )
        self.audit_emitter.emit(
            AuditEvent(
                envelope_id=envelope.envelope_id,
                tool_name=tool_name,
                requested_tier=envelope.authority_ceiling.name,
                effective_tier=effective_tier.name,
                decision="denied",
                duration_ms=duration_ms,
                reason=reason.value,
            )
        )
        return GatewayResponse(
            ok=False,
            envelope_id=envelope.envelope_id,
            tool_name=tool_name,
            denial=denial,
        )
