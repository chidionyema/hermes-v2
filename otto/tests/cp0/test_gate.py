from __future__ import annotations

import json

import pytest

from otto.evals.gate import (
    DEFAULT_THRESHOLDS,
    GateThresholds,
    compare,
    load_thresholds,
)
from otto.evals.models import EvalCase
from otto.evals.report import (
    MalformedReportError,
    read_report,
    validate_report_dict,
    write_report,
)
from otto.evals.runner import run_cases
from otto.tests.cp0.fixtures.fake_agents import (
    agent_improved,
    agent_pass,
    agent_regressed,
)

CASE = EvalCase(
    id="g-1",
    tier="T0",
    task_class="research",
    input={"task": "search for a widget in stock"},
    timeout_s=1.0,
    expect=(
        {"property": "contains", "field": "answer", "value": "widget"},
        {"property": "max_latency_s", "value": 1.0},
        {"property": "max_cost_usd", "value": 0.01},
    ),
)


def _report(agent, tmp_path, name):
    suite_report = run_cases(agent, [CASE], suite="core")
    path = tmp_path / name
    write_report(suite_report, path)
    return read_report(path)


def test_regression_is_detected(tmp_path):
    baseline = _report(agent_pass, tmp_path, "baseline.json")
    candidate = _report(agent_regressed, tmp_path, "candidate.json")
    result = compare(baseline, candidate, DEFAULT_THRESHOLDS)
    assert not result.passed
    assert result.violations
    kinds = {v.kind for v in result.violations}
    assert "case_score_drop" in kinds or "suite_score_drop" in kinds


def test_improvement_passes(tmp_path):
    baseline = _report(agent_pass, tmp_path, "baseline.json")
    candidate = _report(agent_improved, tmp_path, "candidate.json")
    result = compare(baseline, candidate, DEFAULT_THRESHOLDS)
    assert result.passed
    assert result.violations == ()


def test_identical_report_passes(tmp_path):
    baseline = _report(agent_pass, tmp_path, "baseline.json")
    candidate = _report(agent_pass, tmp_path, "candidate.json")
    result = compare(baseline, candidate, DEFAULT_THRESHOLDS)
    assert result.passed


def test_missing_case_in_candidate_is_a_violation(tmp_path):
    baseline = _report(agent_pass, tmp_path, "baseline.json")
    # candidate report has zero cases -- a candidate cannot pass by dropping the hard case.
    empty_suite_report = run_cases(agent_pass, [], suite="core")
    candidate_path = tmp_path / "candidate.json"
    write_report(empty_suite_report, candidate_path)
    candidate = read_report(candidate_path)
    result = compare(baseline, candidate, DEFAULT_THRESHOLDS)
    assert not result.passed
    assert any(v.kind == "missing_case" for v in result.violations)


def test_malformed_report_refused_fail_closed(tmp_path):
    bad_path = tmp_path / "bad.json"
    bad_path.write_text(json.dumps({"schema_version": 1, "suite": "core"}))
    with pytest.raises(MalformedReportError):
        read_report(bad_path)


def test_threshold_config_respected_allows_bounded_regression(tmp_path):
    baseline = _report(agent_pass, tmp_path, "baseline.json")
    candidate = _report(agent_regressed, tmp_path, "candidate.json")

    strict = compare(baseline, candidate, DEFAULT_THRESHOLDS)
    assert not strict.passed

    lenient = GateThresholds(
        max_case_score_drop=1.0,
        max_suite_score_drop=1.0,
        max_dimension_score_drop=1.0,
        max_new_failing_cases=10,
        max_leakage_rate=1.0,
    )
    permissive = compare(baseline, candidate, lenient)
    assert permissive.passed


def test_load_thresholds_from_config_file(tmp_path):
    config_path = tmp_path / "thresholds.json"
    config_path.write_text(json.dumps({"max_suite_score_drop": 0.5}))
    thresholds = load_thresholds(str(config_path))
    assert thresholds.max_suite_score_drop == 0.5
    # untouched fields keep their defaults -- config overrides, never replaces the whole set.
    assert thresholds.max_new_failing_cases == DEFAULT_THRESHOLDS.max_new_failing_cases


def test_load_thresholds_from_env(monkeypatch, tmp_path):
    config_path = tmp_path / "thresholds.yaml"
    config_path.write_text("max_leakage_rate: 0.25\n")
    monkeypatch.setenv("OTTO_EVAL_GATE_CONFIG", str(config_path))
    thresholds = load_thresholds(None)
    assert thresholds.max_leakage_rate == 0.25


def test_load_thresholds_defaults_when_nothing_given(monkeypatch):
    monkeypatch.delenv("OTTO_EVAL_GATE_CONFIG", raising=False)
    assert load_thresholds(None) == DEFAULT_THRESHOLDS


def test_unknown_threshold_field_rejected():
    with pytest.raises(ValueError):
        GateThresholds.from_dict({"not_a_real_field": 1})


def test_validate_report_dict_rejects_non_mapping():
    with pytest.raises(MalformedReportError):
        validate_report_dict(["not", "a", "dict"])
