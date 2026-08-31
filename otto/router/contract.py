"""Universal response contract (spec section 5, "structured outputs everywhere").

Every answer-producing model call is normalised into ``RouterResponse``.
Malformed provider output is REFUSED with ``MalformedProviderOutput`` —
never coerced silently: a missing key is not defaulted, a wrong type is
not cast, an invalid confidence is not rounded to the nearest legal value.
Silent green is the defect class this refusal exists to kill.

Verification status is minted UNVERIFIED here, always. The router holds no
verdict key material (P1); only the Verification Plane can later attach a
verdict, and until it does every claim renders with the unverified marker.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum

_CONFIDENCE_VALUES = ("high", "med", "low")
_TIER_VALUES = ("T0", "T1", "T2", "T3")
_REQUIRED_KEYS = ("answer", "claims", "proposed_actions", "unknowns")


class MalformedProviderOutput(Exception):
    """Provider output does not satisfy the universal response contract."""


class VerificationStatus(str, Enum):
    """P1: the router can mark work unverified; it can never verify it."""

    UNVERIFIED = "unverified"
    VERIFIED = "verified"  # settable only by an external, signed verdict


@dataclass(frozen=True)
class Claim:
    text: str
    evidence_refs: tuple[str, ...]
    confidence: str

    @property
    def has_evidence(self) -> bool:
        return len(self.evidence_refs) > 0


@dataclass(frozen=True)
class ProposedAction:
    tool: str
    args: dict
    tier: str


@dataclass(frozen=True)
class RouterResponse:
    """One normalised model response plus router-side accounting."""

    answer: str
    claims: tuple[Claim, ...]
    proposed_actions: tuple[ProposedAction, ...]
    unknowns: tuple[str, ...]
    lane: str
    model: str
    task_id: str  # ULID; doubles as the trace id (spec section 3)
    cost_usd: float
    tokens: int
    verification: VerificationStatus = VerificationStatus.UNVERIFIED


def _refuse(reason: str) -> MalformedProviderOutput:
    return MalformedProviderOutput(f"provider output refused: {reason}")


def extract_json_object(text: str) -> dict:
    """Strip transport framing (a markdown code fence) and parse the JSON.

    Framing removal is not coercion — the contract is judged on the parsed
    object, and any missing or ill-typed field still refuses below.
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        first_newline = stripped.find("\n")
        if first_newline != -1 and stripped.endswith("```"):
            stripped = stripped[first_newline + 1 : -3].strip()
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise _refuse(f"not valid JSON ({exc.msg})") from exc
    if not isinstance(parsed, dict):
        raise _refuse("top level is not an object")
    return parsed


def _parse_claims(raw: object) -> tuple[Claim, ...]:
    if not isinstance(raw, list):
        raise _refuse("'claims' is not an array")
    claims: list[Claim] = []
    for i, row in enumerate(raw):
        if not isinstance(row, dict):
            raise _refuse(f"claims[{i}] is not an object")
        text = row.get("text")
        refs = row.get("evidence_refs")
        confidence = row.get("confidence")
        if not isinstance(text, str):
            raise _refuse(f"claims[{i}].text missing or not a string")
        if not isinstance(refs, list) or not all(isinstance(r, str) for r in refs):
            raise _refuse(f"claims[{i}].evidence_refs missing or not string array")
        if confidence not in _CONFIDENCE_VALUES:
            raise _refuse(f"claims[{i}].confidence not one of {_CONFIDENCE_VALUES}")
        claims.append(
            Claim(text=text, evidence_refs=tuple(refs), confidence=confidence)
        )
    return tuple(claims)


def _parse_actions(raw: object) -> tuple[ProposedAction, ...]:
    if not isinstance(raw, list):
        raise _refuse("'proposed_actions' is not an array")
    actions: list[ProposedAction] = []
    for i, row in enumerate(raw):
        if not isinstance(row, dict):
            raise _refuse(f"proposed_actions[{i}] is not an object")
        tool = row.get("tool")
        args = row.get("args")
        tier = row.get("tier")
        if not isinstance(tool, str):
            raise _refuse(f"proposed_actions[{i}].tool missing or not a string")
        if not isinstance(args, dict):
            raise _refuse(f"proposed_actions[{i}].args missing or not an object")
        if tier not in _TIER_VALUES:
            raise _refuse(f"proposed_actions[{i}].tier not one of {_TIER_VALUES}")
        actions.append(ProposedAction(tool=tool, args=args, tier=tier))
    return tuple(actions)


def normalise_provider_output(
    raw_text: str,
    *,
    lane: str,
    model: str,
    task_id: str,
    cost_usd: float,
    tokens: int,
) -> RouterResponse:
    """Parse provider text into the universal contract, or refuse.

    Always returns verification=UNVERIFIED (P1): no path through this
    function, and no field a provider can emit, produces a verified
    response.
    """
    obj = extract_json_object(raw_text)
    for k in _REQUIRED_KEYS:
        if k not in obj:
            raise _refuse(f"missing required key '{k}'")
    answer = obj["answer"]
    if not isinstance(answer, str):
        raise _refuse("'answer' is not a string")
    unknowns = obj["unknowns"]
    if not isinstance(unknowns, list) or not all(isinstance(u, str) for u in unknowns):
        raise _refuse("'unknowns' is not a string array")
    return RouterResponse(
        answer=answer,
        claims=_parse_claims(obj["claims"]),
        proposed_actions=_parse_actions(obj["proposed_actions"]),
        unknowns=tuple(unknowns),
        lane=lane,
        model=model,
        task_id=task_id,
        cost_usd=cost_usd,
        tokens=tokens,
        verification=VerificationStatus.UNVERIFIED,
    )
