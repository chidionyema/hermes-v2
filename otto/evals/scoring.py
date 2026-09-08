"""Property checkers. Pure functions, no model calls, no LLM-as-judge (v0).

Every checker has the signature ``(spec: dict, result: EvalResult) -> PropertyOutcome``
and is deterministic: same spec + same result always yields the same outcome.
Score is the fraction of a case's ``expect`` list that passes -- never a
similarity score to a golden string, per the founder's "expected properties,
not exact strings" instruction.

Each property is mapped to one of the spec's five graded dimensions
(section 11: correctness, groundedness, tool-path validity, latency, cost)
so a report can show a per-dimension breakdown, not just one number.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from otto.evals.models import EvalResult


@dataclass(frozen=True)
class PropertyOutcome:
    property: str
    passed: bool
    detail: str


class UnknownPropertyError(ValueError):
    """Raised when an eval case names a property this scorer does not implement."""


def _check_contains(spec: dict, result: EvalResult) -> PropertyOutcome:
    field_name = spec.get("field", "answer")
    value = str(spec["value"])
    haystack = str(result.field_value(field_name) or "")
    passed = value.lower() in haystack.lower()
    return PropertyOutcome("contains", passed, f"{value!r} in {field_name}: {passed}")


def _check_not_contains(spec: dict, result: EvalResult) -> PropertyOutcome:
    field_name = spec.get("field", "answer")
    value = str(spec["value"])
    haystack = str(result.field_value(field_name) or "")
    passed = value.lower() not in haystack.lower()
    return PropertyOutcome(
        "not_contains", passed, f"{value!r} absent from {field_name}: {passed}"
    )


def _check_regex(spec: dict, result: EvalResult) -> PropertyOutcome:
    field_name = spec.get("field", "answer")
    pattern = str(spec["value"])
    haystack = str(result.field_value(field_name) or "")
    passed = re.search(pattern, haystack) is not None
    return PropertyOutcome(
        "regex", passed, f"/{pattern}/ matches {field_name}: {passed}"
    )


def _check_tool_path(spec: dict, result: EvalResult) -> PropertyOutcome:
    expected = list(spec["value"])
    mode = spec.get("mode", "exact")
    actual = list(result.tool_calls)
    if mode == "exact":
        passed = actual == expected
    elif mode == "subset":
        passed = set(expected).issubset(set(actual))
    elif mode == "no_flailing":
        # tool-path validity: every call actually used is in the allowed set,
        # and none is repeated more than the expected count -- catches an
        # agent hammering the same tool instead of reasoning.
        allowed = set(expected)
        passed = (
            all(t in allowed for t in actual)
            and len(actual) <= max(len(expected), 1) * 2
        )
    else:
        raise UnknownPropertyError(f"tool_path: unknown mode {mode!r}")
    return PropertyOutcome(
        "tool_path", passed, f"mode={mode} expected={expected} actual={actual}"
    )


def _check_max_latency_s(spec: dict, result: EvalResult) -> PropertyOutcome:
    limit = float(spec["value"])
    passed = result.latency_s <= limit
    return PropertyOutcome(
        "max_latency_s", passed, f"{result.latency_s}s <= {limit}s: {passed}"
    )


def _check_max_cost_usd(spec: dict, result: EvalResult) -> PropertyOutcome:
    limit = float(spec["value"])
    passed = result.cost_usd <= limit
    return PropertyOutcome(
        "max_cost_usd", passed, f"${result.cost_usd} <= ${limit}: {passed}"
    )


def _check_exit_code(spec: dict, result: EvalResult) -> PropertyOutcome:
    expected = int(spec["value"])
    passed = result.exit_code == expected
    return PropertyOutcome(
        "exit_code", passed, f"{result.exit_code} == {expected}: {passed}"
    )


def _check_groundedness_ratio(spec: dict, result: EvalResult) -> PropertyOutcome:
    """Every claim's evidence_refs must resolve -- checked mechanically (spec section 11).

    v0 mechanical check: non-empty evidence_refs list. A real "does the
    evidence actually support the claim" check needs the Verification Plane
    (Phase 2); this dimension here only rules out unsupported claims, it
    does not confirm the support is valid -- that gap is stated in the
    report so it is never mistaken for the full P1/P3 guarantee.
    """
    min_ratio = float(spec.get("min_ratio", 1.0))
    claims = result.claims
    if not claims:
        return PropertyOutcome(
            "groundedness_ratio", True, "no claims made: vacuously grounded"
        )
    grounded = sum(1 for c in claims if c.evidence_refs)
    ratio = grounded / len(claims)
    passed = ratio >= min_ratio
    return PropertyOutcome(
        "groundedness_ratio",
        passed,
        f"{grounded}/{len(claims)}={ratio:.2f} >= {min_ratio}: {passed}",
    )


def _check_no_leakage(spec: dict, result: EvalResult) -> PropertyOutcome:
    """P1 (no self-certification): a task must never claim completed without a passing verdict.

    This is the false-success-set check the spec targets at 0% leakage
    (section 11). It is a property of the result, never a judgment call.
    """
    leaked = result.completed_claimed and result.verdict_passed is not True
    return PropertyOutcome(
        "no_leakage",
        not leaked,
        f"completed_claimed={result.completed_claimed} verdict_passed={result.verdict_passed}",
    )


def _check_no_error(spec: dict, result: EvalResult) -> PropertyOutcome:
    passed = result.error is None
    return PropertyOutcome("no_error", passed, f"error={result.error!r}")


CHECKERS: dict[str, Callable[[dict, EvalResult], PropertyOutcome]] = {
    "contains": _check_contains,
    "not_contains": _check_not_contains,
    "regex": _check_regex,
    "tool_path": _check_tool_path,
    "max_latency_s": _check_max_latency_s,
    "max_cost_usd": _check_max_cost_usd,
    "exit_code": _check_exit_code,
    "groundedness_ratio": _check_groundedness_ratio,
    "no_leakage": _check_no_leakage,
    "no_error": _check_no_error,
}

# spec section 11's five graded dimensions, plus groundedness's false-success cousin.
DIMENSION_OF_PROPERTY: dict[str, str] = {
    "contains": "correctness",
    "not_contains": "correctness",
    "regex": "correctness",
    "exit_code": "correctness",
    "no_error": "correctness",
    "tool_path": "tool_path_validity",
    "max_latency_s": "latency",
    "max_cost_usd": "cost",
    "groundedness_ratio": "groundedness",
    "no_leakage": "groundedness",
}


def check_property(spec: dict, result: EvalResult) -> PropertyOutcome:
    name = spec.get("property")
    checker = CHECKERS.get(name)
    if checker is None:
        raise UnknownPropertyError(
            f"no checker registered for property {name!r} (known: {sorted(CHECKERS)})"
        )
    return checker(spec, result)


def dimension_for(property_name: str) -> str:
    return DIMENSION_OF_PROPERTY.get(property_name, "correctness")
