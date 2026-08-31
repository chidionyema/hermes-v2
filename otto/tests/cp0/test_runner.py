from __future__ import annotations

import time

from otto.evals.models import EvalCase, EvalResult, load_suite
from otto.evals.runner import run_case, run_cases, run_suite_dir
from otto.tests.cp0.fixtures.fake_agents import (
    agent_leaky,
    agent_pass,
    agent_raises,
    agent_regressed,
    agent_timeout,
)

SIMPLE_CASE = EvalCase(
    id="simple",
    tier="T0",
    task_class="research",
    input={"task": "x"},
    timeout_s=1.0,
    expect=({"property": "exit_code", "value": 0},),
)


def test_run_case_pass():
    report = run_case(agent_pass, SIMPLE_CASE)
    assert report.passed
    assert report.score == 1.0
    assert not report.timed_out
    assert report.agent_error is None


def test_run_case_fail_is_scored_not_raised():
    report = run_case(agent_regressed, SIMPLE_CASE)
    assert not report.passed
    assert report.score == 0.0


def test_timeout_is_enforced():
    """Explicit LAW-46/verification requirement: a hung agent must not hang the runner."""
    short = EvalCase(
        id="times-out",
        tier="T0",
        task_class="research",
        input={"task": "x"},
        timeout_s=0.05,
        expect=({"property": "exit_code", "value": 0},),
    )
    start = time.monotonic()
    report = run_case(agent_timeout, short)
    elapsed = time.monotonic() - start
    assert report.timed_out
    assert report.score == 0.0
    assert not report.passed
    # the runner must return close to the case's own timeout, not wait for the agent's sleep
    assert elapsed < 5.0


def test_agent_exception_is_captured_not_raised():
    report = run_case(agent_raises, SIMPLE_CASE)
    assert not report.passed
    assert report.score == 0.0
    assert report.agent_error is not None
    assert "RuntimeError" in report.agent_error


def test_run_cases_aggregates_multiple(suite_basic_dir):
    cases = load_suite(suite_basic_dir)

    def router(case: EvalCase) -> EvalResult:
        if "false-success" in case.id:
            return agent_leaky(case)
        return agent_pass(case)

    suite_report = run_cases(router, cases, suite="core")
    assert len(suite_report.cases) == len(cases)
    assert 0.0 <= suite_report.suite_score <= 1.0


def test_leakage_rate_computed_only_over_false_success_cases(suite_basic_dir):
    cases = load_suite(suite_basic_dir)

    def router(case: EvalCase) -> EvalResult:
        if "false_success" in case.tags:
            return agent_leaky(case)
        return agent_pass(case)

    suite_report = run_cases(router, cases, suite="core")
    assert suite_report.leakage_rate == 1.0


def test_leakage_rate_zero_when_no_false_success_cases():
    report = run_cases(agent_pass, [SIMPLE_CASE], suite="core")
    assert report.leakage_rate == 0.0


def test_per_dimension_score_shape(suite_basic_dir):
    suite_report = run_suite_dir(agent_pass, suite_basic_dir, suite="core")
    dims = suite_report.per_dimension_score
    assert set(dims).issubset(
        {"correctness", "tool_path_validity", "latency", "cost", "groundedness"}
    )
    assert all(0.0 <= v <= 1.0 for v in dims.values())


def test_timed_out_case_zeroes_every_dimension_it_would_have_graded():
    case = EvalCase(
        id="times-out-2",
        tier="T0",
        task_class="research",
        input={"task": "x"},
        timeout_s=0.05,
        expect=(
            {"property": "max_latency_s", "value": 1.0},
            {"property": "exit_code", "value": 0},
        ),
    )
    report = run_cases(agent_timeout, [case], suite="core")
    assert report.per_dimension_score["latency"] == 0.0
    assert report.per_dimension_score["correctness"] == 0.0
