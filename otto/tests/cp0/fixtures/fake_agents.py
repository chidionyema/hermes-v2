"""Fake agent-under-test callables for CP0 harness tests. No model calls, no I/O.

Each is ``Callable[[EvalCase], EvalResult]`` -- the exact interface the real
Otto orchestrator will satisfy once it exists. These stand in for it so the
harness itself can be tested in isolation (spec Phase 0 does not require a
live orchestrator to prove the eval harness works).
"""

from __future__ import annotations

import time

from otto.evals.models import Claim, EvalCase, EvalResult


def agent_pass(case: EvalCase) -> EvalResult:
    """Satisfies every property in fixtures/suite_basic and suite_regression's baseline case."""
    return EvalResult(
        answer="the search turned up a widget in stock",
        claims=(Claim("a widget is in stock", evidence_refs=("tool:web_search:1",)),),
        tool_calls=("web_search",),
        latency_s=0.01,
        cost_usd=0.001,
        exit_code=0,
        completed_claimed=True,
        verdict_passed=True,
    )


def agent_regressed(case: EvalCase) -> EvalResult:
    """Deliberately worse than agent_pass: wrong answer, no evidence, over budget."""
    return EvalResult(
        answer="nothing found",
        claims=(Claim("something", evidence_refs=()),),
        tool_calls=("web_search", "web_search", "web_search"),
        latency_s=9.0,
        cost_usd=5.0,
        exit_code=1,
        completed_claimed=True,
        verdict_passed=False,
    )


def agent_improved(case: EvalCase) -> EvalResult:
    """Strictly better than agent_pass on every measured dimension."""
    return EvalResult(
        answer="the search turned up a widget in stock, fast",
        claims=(
            Claim(
                "a widget is in stock",
                evidence_refs=("tool:web_search:1", "tool:web_search:2"),
            ),
        ),
        tool_calls=("web_search",),
        latency_s=0.001,
        cost_usd=0.0001,
        exit_code=0,
        completed_claimed=True,
        verdict_passed=True,
    )


def agent_timeout(case: EvalCase) -> EvalResult:
    """Sleeps well past any case timeout used in tests; runner must not wait for it."""
    time.sleep(case.timeout_s + 30)
    return EvalResult()  # pragma: no cover - never reached inside the timeout window


def agent_raises(case: EvalCase) -> EvalResult:
    raise RuntimeError("agent under test blew up")


def agent_leaky(case: EvalCase) -> EvalResult:
    """Claims completed with no passing verdict -- the P1 self-certification leak."""
    return EvalResult(
        answer="all done",
        claims=(),
        tool_calls=(),
        latency_s=0.01,
        cost_usd=0.0,
        exit_code=0,
        completed_claimed=True,
        verdict_passed=False,
    )
