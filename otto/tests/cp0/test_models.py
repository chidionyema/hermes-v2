from __future__ import annotations

import pytest

from otto.evals.models import (
    EvalResult,
    MalformedCaseError,
    case_from_dict,
    load_case,
    load_suite,
)

VALID_CASE = {
    "id": "x-1",
    "tier": "T0",
    "task_class": "research",
    "input": {"task": "do a thing"},
    "timeout_s": 1,
    "expect": [{"property": "exit_code", "value": 0}],
}


def test_valid_case_loads():
    case = case_from_dict(VALID_CASE)
    assert case.id == "x-1"
    assert case.tier == "T0"
    assert case.network_degradation == "none"
    assert case.bandwidth_degradation == "none"
    assert case.tags == ()


@pytest.mark.parametrize(
    "missing", ["id", "tier", "task_class", "input", "timeout_s", "expect"]
)
def test_missing_required_field_is_refused(missing):
    raw = dict(VALID_CASE)
    del raw[missing]
    with pytest.raises(MalformedCaseError):
        case_from_dict(raw)


def test_invalid_tier_is_refused():
    raw = dict(VALID_CASE, tier="T9")
    with pytest.raises(MalformedCaseError):
        case_from_dict(raw)


def test_invalid_task_class_is_refused():
    raw = dict(VALID_CASE, task_class="not_a_class")
    with pytest.raises(MalformedCaseError):
        case_from_dict(raw)


def test_zero_timeout_is_refused():
    raw = dict(VALID_CASE, timeout_s=0)
    with pytest.raises(MalformedCaseError):
        case_from_dict(raw)


def test_empty_expect_is_refused():
    raw = dict(VALID_CASE, expect=[])
    with pytest.raises(MalformedCaseError):
        case_from_dict(raw)


def test_not_a_mapping_is_refused():
    with pytest.raises(MalformedCaseError):
        case_from_dict(["not", "a", "dict"])  # type: ignore[arg-type]


def test_invalid_network_degradation_refused():
    raw = dict(VALID_CASE, network_degradation="teleport")
    with pytest.raises(MalformedCaseError):
        case_from_dict(raw)


def test_invalid_bandwidth_degradation_refused():
    raw = dict(VALID_CASE, bandwidth_degradation="teleport")
    with pytest.raises(MalformedCaseError):
        case_from_dict(raw)


def test_load_suite_from_yaml_fixtures(suite_basic_dir):
    cases = load_suite(suite_basic_dir)
    ids = [c.id for c in cases]
    assert ids == sorted(ids), (
        "load_suite must return cases sorted by id for determinism"
    )
    assert "cp0-001-test" in ids
    assert "cp0-002-edge-zero-claims" in ids
    edge = next(c for c in cases if c.id == "cp0-002-edge-zero-claims")
    assert edge.is_edge_case()
    network = next(c for c in cases if c.id == "cp0-003-network-partition")
    assert network.network_degradation == "partition"
    bandwidth = next(c for c in cases if c.id == "cp0-004-bandwidth-throttled")
    assert bandwidth.bandwidth_degradation == "throttled"
    false_success = next(c for c in cases if c.id == "cp0-005-false-success-no-verdict")
    assert false_success.is_false_success_case()


def test_load_suite_rejects_duplicate_ids(tmp_path):
    (tmp_path / "a.yaml").write_text(
        "id: dup\ntier: T0\ntask_class: research\ninput: {task: x}\ntimeout_s: 1\n"
        "expect: [{property: exit_code, value: 0}]\n"
    )
    (tmp_path / "b.yaml").write_text(
        "id: dup\ntier: T0\ntask_class: research\ninput: {task: y}\ntimeout_s: 1\n"
        "expect: [{property: exit_code, value: 0}]\n"
    )
    with pytest.raises(MalformedCaseError):
        load_suite(tmp_path)


def test_load_case_rejects_unsupported_extension(tmp_path):
    bad = tmp_path / "case.txt"
    bad.write_text("id: x")
    with pytest.raises(MalformedCaseError):
        load_case(bad)


def test_load_case_rejects_invalid_yaml(tmp_path):
    bad = tmp_path / "case.yaml"
    bad.write_text("id: [this is not: valid: yaml:::")
    with pytest.raises(MalformedCaseError):
        load_case(bad)


def test_eval_result_field_value_reads_extra():
    result = EvalResult(answer="hi", extra={"custom_field": "42"})
    assert result.field_value("answer") == "hi"
    assert result.field_value("custom_field") == "42"
    assert result.field_value("missing") is None
