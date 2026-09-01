"""Baseline-vs-candidate regression gate (spec P6: "Evals gate change").

Thresholds are configuration with defaults, never constants baked into the
comparison (founder, 2026-08-31: "configurable obvs"). Defaults live in
``DEFAULT_THRESHOLDS`` below and can be overridden per field by a YAML/JSON
config file (``--config`` / ``OTTO_EVAL_GATE_CONFIG``) or by keyword when
called as a library.

Fails closed: a malformed baseline or candidate report is refused before any
comparison runs (see ``otto.evals.report.read_report``), and a case present
in the baseline but missing from the candidate counts as a regression to
that case (a candidate cannot pass by silently dropping a hard case).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

import yaml

GATE_CONFIG_ENV_VAR = "OTTO_EVAL_GATE_CONFIG"


@dataclass(frozen=True)
class GateThresholds:
    """Every field has a default; every field is overridable. Nothing here is a constant used
    directly in the comparison logic below -- the comparison always reads through an instance
    of this dataclass, per-field, so a config file can move any one of them independently.
    """

    max_case_score_drop: float = 0.0
    max_suite_score_drop: float = 0.02
    max_dimension_score_drop: float = 0.02
    max_new_failing_cases: int = 0
    max_leakage_rate: float = 0.0

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "GateThresholds":
        known = {f.name for f in fields(cls)}
        unknown = set(raw) - known
        if unknown:
            raise ValueError(
                f"unknown threshold field(s): {sorted(unknown)} (known: {sorted(known)})"
            )
        return cls(**raw)


DEFAULT_THRESHOLDS = GateThresholds()


def load_thresholds(path: str | Path | None) -> GateThresholds:
    """Load thresholds from a config file if given (or from OTTO_EVAL_GATE_CONFIG), else defaults."""
    value = path or os.environ.get(GATE_CONFIG_ENV_VAR)
    if not value:
        return DEFAULT_THRESHOLDS
    text = Path(value).read_text(encoding="utf-8")
    raw = (
        yaml.safe_load(text)
        if str(value).endswith((".yaml", ".yml"))
        else json.loads(text)
    )
    return GateThresholds.from_dict(raw or {})


@dataclass(frozen=True)
class Violation:
    kind: str
    detail: str


@dataclass(frozen=True)
class GateResult:
    passed: bool
    violations: tuple[Violation, ...]

    def summary(self) -> str:
        if self.passed:
            return "PASS: no regression beyond configured thresholds"
        lines = [
            f"FAIL: {len(self.violations)} regression(s) beyond configured thresholds"
        ]
        lines += [f"  - [{v.kind}] {v.detail}" for v in self.violations]
        return "\n".join(lines)


def compare(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    thresholds: GateThresholds = DEFAULT_THRESHOLDS,
) -> GateResult:
    """Compare two *validated* report dicts (already passed through report.validate_report_dict)."""
    violations: list[Violation] = []

    base_suite_score = baseline["aggregate"]["suite_score"]
    cand_suite_score = candidate["aggregate"]["suite_score"]
    suite_drop = base_suite_score - cand_suite_score
    if suite_drop > thresholds.max_suite_score_drop:
        violations.append(
            Violation(
                "suite_score_drop",
                f"suite score dropped {suite_drop:.4f} (baseline {base_suite_score:.4f} -> "
                f"candidate {cand_suite_score:.4f}), threshold {thresholds.max_suite_score_drop}",
            )
        )

    base_dims = baseline["aggregate"].get("per_dimension_score", {})
    cand_dims = candidate["aggregate"].get("per_dimension_score", {})
    for dim, base_val in sorted(base_dims.items()):
        cand_val = cand_dims.get(dim, 0.0)
        drop = base_val - cand_val
        if drop > thresholds.max_dimension_score_drop:
            violations.append(
                Violation(
                    "dimension_score_drop",
                    f"dimension {dim!r} dropped {drop:.4f} (baseline {base_val:.4f} -> "
                    f"candidate {cand_val:.4f}), threshold {thresholds.max_dimension_score_drop}",
                )
            )

    base_leakage = baseline["aggregate"].get("leakage_rate", 0.0)
    cand_leakage = candidate["aggregate"].get("leakage_rate", 0.0)
    if cand_leakage > thresholds.max_leakage_rate:
        violations.append(
            Violation(
                "leakage_rate",
                f"candidate leakage_rate {cand_leakage:.4f} exceeds threshold {thresholds.max_leakage_rate} "
                f"(baseline was {base_leakage:.4f})",
            )
        )

    base_cases = {c["case_id"]: c for c in baseline["cases"]}
    cand_cases = {c["case_id"]: c for c in candidate["cases"]}

    new_failures: list[str] = []
    for case_id, base_case in sorted(base_cases.items()):
        cand_case = cand_cases.get(case_id)
        if cand_case is None:
            new_failures.append(case_id)
            violations.append(
                Violation(
                    "missing_case",
                    f"case {case_id!r} present in baseline, absent from candidate",
                )
            )
            continue
        drop = base_case["score"] - cand_case["score"]
        if drop > thresholds.max_case_score_drop:
            violations.append(
                Violation(
                    "case_score_drop",
                    f"case {case_id!r} dropped {drop:.4f} (baseline {base_case['score']:.4f} -> "
                    f"candidate {cand_case['score']:.4f}), threshold {thresholds.max_case_score_drop}",
                )
            )
        if base_case["passed"] and not cand_case["passed"]:
            new_failures.append(case_id)

    if len(new_failures) > thresholds.max_new_failing_cases:
        violations.append(
            Violation(
                "new_failing_cases",
                f"{len(new_failures)} case(s) newly failing: {sorted(set(new_failures))}, "
                f"threshold {thresholds.max_new_failing_cases}",
            )
        )

    return GateResult(passed=not violations, violations=tuple(violations))
