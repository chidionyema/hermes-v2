"""Structured denial — a refusal is data, never silence.

Task requirement: "a task envelope below the tier is refused with a
structured denial (never silent)". Every refusal path in the gateway
produces one of these, and every one of them is also audited.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class DenialReason(str, Enum):
    UNKNOWN_TOOL = "UNKNOWN_TOOL"
    SCHEMA_INVALID = "SCHEMA_INVALID"
    TIER_INSUFFICIENT = "TIER_INSUFFICIENT"
    TAINT_CAP = "TAINT_CAP"
    HUMAN_APPROVAL_REQUIRED = "HUMAN_APPROVAL_REQUIRED"
    HUMAN_APPROVAL_REFUSED = "HUMAN_APPROVAL_REFUSED"


@dataclass(frozen=True)
class Denial:
    """A structured, machine-checkable refusal.

    ``requested_tier`` is what the envelope claimed; ``effective_tier`` is
    what it was actually evaluated against (after the taint cap, spec P5).
    The two are reported separately so a caller — or a test — can tell a
    taint-capped refusal from a genuine under-provisioning.
    """

    reason: DenialReason
    message: str
    envelope_id: str
    tool_name: str
    requested_tier: str
    effective_tier: str
