"""Eval case and eval result data shapes (spec section 11).

An eval case is loaded from YAML or JSON. It never encodes an exact string
answer -- it encodes measurable *properties* the agent's result must satisfy
(spec: "correctness; groundedness; tool-path validity; latency; cost").

Tiers (``T0``..``T3``) and task classes (``research`` / ``code`` /
``ops_read`` / ``comms`` / ``schedule`` / ``memory``) mirror spec sections 3
and 9 so a case can be labelled the same way a real task envelope is.

Founder-named coverage (verbatim from his prompt): "TEST", "EDGE CASE",
"NETWORK", "BANDWIDTH". These are represented as:
  - ``tags`` containing ``"edge_case"`` for engineered edge cases.
  - ``network_degradation`` in {"none", "latency", "partition"}.
  - ``bandwidth_degradation`` in {"none", "throttled"}.
A plain case with no tag and no degradation is the "TEST" (happy path) case.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REQUIRED_CASE_FIELDS = ("id", "tier", "task_class", "input", "timeout_s", "expect")
VALID_TIERS = ("T0", "T1", "T2", "T3")
VALID_TASK_CLASSES = (
    "research",
    "code",
    "ops_read",
    "comms",
    "schedule",
    "memory",
)
VALID_NETWORK_DEGRADATION = ("none", "latency", "partition")
VALID_BANDWIDTH_DEGRADATION = ("none", "throttled")


class MalformedCaseError(ValueError):
    """Raised when an eval case file fails to parse into a well-formed EvalCase."""


@dataclass(frozen=True)
class EvalCase:
    """One eval case: a task to run and the measurable properties expected of it."""

    id: str
    tier: str
    task_class: str
    input: dict[str, Any]
    timeout_s: float
    expect: tuple[dict[str, Any], ...]
    network_degradation: str = "none"
    bandwidth_degradation: str = "none"
    tags: tuple[str, ...] = ()

    def is_edge_case(self) -> bool:
        return "edge_case" in self.tags

    def is_false_success_case(self) -> bool:
        return "false_success" in self.tags


def _validate_raw(raw: Any, source: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise MalformedCaseError(
            f"{source}: eval case must be a mapping, got {type(raw).__name__}"
        )
    missing = [f for f in REQUIRED_CASE_FIELDS if f not in raw]
    if missing:
        raise MalformedCaseError(
            f"{source}: missing required field(s): {', '.join(missing)}"
        )
    if raw["tier"] not in VALID_TIERS:
        raise MalformedCaseError(
            f"{source}: tier must be one of {VALID_TIERS}, got {raw['tier']!r}"
        )
    if raw["task_class"] not in VALID_TASK_CLASSES:
        raise MalformedCaseError(
            f"{source}: task_class must be one of {VALID_TASK_CLASSES}, got {raw['task_class']!r}"
        )
    if not isinstance(raw["input"], dict):
        raise MalformedCaseError(f"{source}: input must be a mapping")
    try:
        timeout_s = float(raw["timeout_s"])
    except (TypeError, ValueError) as exc:
        raise MalformedCaseError(f"{source}: timeout_s must be numeric") from exc
    if timeout_s <= 0:
        raise MalformedCaseError(f"{source}: timeout_s must be > 0, got {timeout_s}")
    if not isinstance(raw["expect"], list) or not raw["expect"]:
        raise MalformedCaseError(
            f"{source}: expect must be a non-empty list of property checks"
        )
    for i, item in enumerate(raw["expect"]):
        if not isinstance(item, dict) or "property" not in item:
            raise MalformedCaseError(
                f"{source}: expect[{i}] must be a mapping with a 'property' key"
            )
    net = raw.get("network_degradation", "none")
    if net not in VALID_NETWORK_DEGRADATION:
        raise MalformedCaseError(
            f"{source}: network_degradation must be one of {VALID_NETWORK_DEGRADATION}"
        )
    bw = raw.get("bandwidth_degradation", "none")
    if bw not in VALID_BANDWIDTH_DEGRADATION:
        raise MalformedCaseError(
            f"{source}: bandwidth_degradation must be one of {VALID_BANDWIDTH_DEGRADATION}"
        )
    tags = raw.get("tags", [])
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        raise MalformedCaseError(f"{source}: tags must be a list of strings")
    return raw


def case_from_dict(raw: dict[str, Any], source: str = "<dict>") -> EvalCase:
    validated = _validate_raw(raw, source)
    return EvalCase(
        id=str(validated["id"]),
        tier=validated["tier"],
        task_class=validated["task_class"],
        input=dict(validated["input"]),
        timeout_s=float(validated["timeout_s"]),
        expect=tuple(dict(item) for item in validated["expect"]),
        network_degradation=validated.get("network_degradation", "none"),
        bandwidth_degradation=validated.get("bandwidth_degradation", "none"),
        tags=tuple(validated.get("tags", [])),
    )


def load_case(path: Path) -> EvalCase:
    """Load a single eval case from a .yaml/.yml/.json file."""
    text = path.read_text(encoding="utf-8")
    try:
        if path.suffix in (".yaml", ".yml"):
            raw = yaml.safe_load(text)
        elif path.suffix == ".json":
            raw = json.loads(text)
        else:
            raise MalformedCaseError(
                f"{path}: unsupported extension {path.suffix!r} (use .yaml/.yml/.json)"
            )
    except (yaml.YAMLError, json.JSONDecodeError) as exc:
        raise MalformedCaseError(f"{path}: could not parse: {exc}") from exc
    return case_from_dict(raw, source=str(path))


def load_suite(directory: Path) -> list[EvalCase]:
    """Load every case file in a directory, sorted by case id for determinism."""
    paths = sorted(
        p
        for p in directory.iterdir()
        if p.suffix in (".yaml", ".yml", ".json") and p.is_file()
    )
    cases = [load_case(p) for p in paths]
    seen: set[str] = set()
    dupes = []
    for c in cases:
        if c.id in seen:
            dupes.append(c.id)
        seen.add(c.id)
    if dupes:
        raise MalformedCaseError(
            f"{directory}: duplicate case id(s): {sorted(set(dupes))}"
        )
    return sorted(cases, key=lambda c: c.id)


@dataclass(frozen=True)
class Claim:
    """One claim in an agent's answer, and where it says the claim came from.

    Mirrors spec section 5's universal response contract:
    ``{"text": ..., "evidence_refs": [...], "confidence": ...}``.
    """

    text: str
    evidence_refs: tuple[str, ...] = ()
    confidence: str = "med"


@dataclass(frozen=True)
class EvalResult:
    """What the agent-under-test callable returns for one eval case.

    This is intentionally NOT the full Otto task envelope (spec section 3)
    -- Phase 0 does not require a live orchestrator to grade an eval case,
    only these measurable fields.
    """

    answer: str = ""
    claims: tuple[Claim, ...] = ()
    tool_calls: tuple[str, ...] = ()
    latency_s: float = 0.0
    cost_usd: float = 0.0
    exit_code: int = 0
    completed_claimed: bool = True
    verdict_passed: bool | None = None
    error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def field_value(self, name: str) -> Any:
        """Resolve a field named in a case's ``expect`` entry, including extras."""
        if hasattr(self, name) and name != "extra":
            return getattr(self, name)
        return self.extra.get(name)
