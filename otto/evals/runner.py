"""Runs a suite of eval cases against a pluggable agent-under-test callable.

The agent under test is any ``Callable[[EvalCase], EvalResult]``. The runner
knows nothing about how the agent works -- it only calls it, enforces the
case's timeout, and scores the returned ``EvalResult`` against the case's
``expect`` list using ``otto.evals.scoring`` (no LLM-as-judge, see the
package docstring's DSPy/R64 note).

Timeout enforcement runs the agent call on a daemon thread so a hung agent
under test can never block the runner or leak a non-daemon process past the
test run (see the estate lesson: a killed pytest run must not leave an
orphaned long-lived process behind).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from otto.evals.models import EvalCase, EvalResult, load_suite
from otto.evals.scoring import PropertyOutcome, check_property, dimension_for

AgentUnderTest = (
    Any  # Callable[[EvalCase], EvalResult]; kept loose to avoid a hard import cycle.
)


@dataclass(frozen=True)
class CaseReport:
    case_id: str
    tier: str
    task_class: str
    tags: tuple[str, ...]
    passed: bool
    score: float
    timed_out: bool
    elapsed_s: float
    outcomes: tuple[PropertyOutcome, ...]
    agent_error: str | None = None


@dataclass(frozen=True)
class SuiteReport:
    suite: str
    cases: tuple[CaseReport, ...]

    @property
    def suite_score(self) -> float:
        if not self.cases:
            return 0.0
        return sum(c.score for c in self.cases) / len(self.cases)

    @property
    def per_dimension_score(self) -> dict[str, float]:
        totals: dict[str, list[float]] = {}
        for case in self.cases:
            if case.timed_out:
                # a timeout fails every dimension the case would have graded.
                for outcome in case.outcomes:
                    dim = dimension_for(outcome.property)
                    totals.setdefault(dim, []).append(0.0)
                continue
            for outcome in case.outcomes:
                dim = dimension_for(outcome.property)
                totals.setdefault(dim, []).append(1.0 if outcome.passed else 0.0)
        return {
            dim: (sum(vals) / len(vals) if vals else 0.0)
            for dim, vals in sorted(totals.items())
        }

    @property
    def leakage_rate(self) -> float:
        """Fraction of false-success-tagged cases where the no_leakage property failed."""
        fs_cases = [c for c in self.cases if "false_success" in c.tags]
        if not fs_cases:
            return 0.0
        leaked = 0
        for case in fs_cases:
            for outcome in case.outcomes:
                if outcome.property == "no_leakage" and not outcome.passed:
                    leaked += 1
                    break
        return leaked / len(fs_cases)


def _run_with_timeout(
    agent: AgentUnderTest, case: EvalCase, timeout_s: float
) -> tuple[EvalResult | None, bool, str | None]:
    """Run agent(case) on a daemon thread; return (result, timed_out, error)."""
    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["value"] = agent(case)
        except Exception as exc:  # noqa: BLE001 - the agent under test is untrusted code
            box["error"] = f"{type(exc).__name__}: {exc}"

    thread = threading.Thread(target=target, daemon=True, name=f"otto-eval-{case.id}")
    thread.start()
    thread.join(timeout_s)
    if thread.is_alive():
        return None, True, None
    if "error" in box:
        return None, False, box["error"]
    return box.get("value"), False, None


def run_case(agent: AgentUnderTest, case: EvalCase) -> CaseReport:
    start = time.monotonic()
    result, timed_out, agent_error = _run_with_timeout(agent, case, case.timeout_s)
    elapsed = time.monotonic() - start

    if timed_out:
        outcomes = tuple(
            PropertyOutcome(
                spec.get("property", "?"), False, f"case timed out at {case.timeout_s}s"
            )
            for spec in case.expect
        )
        return CaseReport(
            case_id=case.id,
            tier=case.tier,
            task_class=case.task_class,
            tags=case.tags,
            passed=False,
            score=0.0,
            timed_out=True,
            elapsed_s=elapsed,
            outcomes=outcomes,
        )

    if agent_error is not None or result is None:
        outcomes = tuple(
            PropertyOutcome(
                spec.get("property", "?"), False, f"agent raised: {agent_error}"
            )
            for spec in case.expect
        )
        return CaseReport(
            case_id=case.id,
            tier=case.tier,
            task_class=case.task_class,
            tags=case.tags,
            passed=False,
            score=0.0,
            timed_out=False,
            elapsed_s=elapsed,
            outcomes=outcomes,
            agent_error=agent_error or "agent returned no result",
        )

    outcomes = tuple(check_property(spec, result) for spec in case.expect)
    score = sum(1 for o in outcomes if o.passed) / len(outcomes) if outcomes else 1.0
    return CaseReport(
        case_id=case.id,
        tier=case.tier,
        task_class=case.task_class,
        tags=case.tags,
        passed=score == 1.0,
        score=score,
        timed_out=False,
        elapsed_s=elapsed,
        outcomes=outcomes,
    )


def run_cases(
    agent: AgentUnderTest, cases: list[EvalCase], suite: str = "core"
) -> SuiteReport:
    return SuiteReport(suite=suite, cases=tuple(run_case(agent, c) for c in cases))


def run_suite_dir(
    agent: AgentUnderTest, directory: Path, suite: str = "core"
) -> SuiteReport:
    cases = load_suite(directory)
    return run_cases(agent, cases, suite=suite)
