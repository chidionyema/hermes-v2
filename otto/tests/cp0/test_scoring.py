from __future__ import annotations

import pytest

from otto.evals.models import Claim, EvalResult
from otto.evals.scoring import UnknownPropertyError, check_property, dimension_for


def make_result(**overrides):
    defaults = dict(
        answer="a widget is in stock",
        claims=(Claim("a widget is in stock", evidence_refs=("ref:1",)),),
        tool_calls=("web_search",),
        latency_s=0.5,
        cost_usd=0.01,
        exit_code=0,
        completed_claimed=True,
        verdict_passed=True,
    )
    defaults.update(overrides)
    return EvalResult(**defaults)


def test_contains_passes_and_fails():
    result = make_result()
    assert check_property(
        {"property": "contains", "field": "answer", "value": "widget"}, result
    ).passed
    assert not check_property(
        {"property": "contains", "field": "answer", "value": "gadget"}, result
    ).passed


def test_contains_is_case_insensitive():
    result = make_result(answer="A WIDGET")
    assert check_property(
        {"property": "contains", "field": "answer", "value": "widget"}, result
    ).passed


def test_not_contains():
    result = make_result()
    assert check_property(
        {"property": "not_contains", "field": "answer", "value": "gadget"}, result
    ).passed
    assert not check_property(
        {"property": "not_contains", "field": "answer", "value": "widget"}, result
    ).passed


def test_regex():
    result = make_result(answer="order #12345 shipped")
    assert check_property(
        {"property": "regex", "field": "answer", "value": r"#\d+"}, result
    ).passed
    assert not check_property(
        {"property": "regex", "field": "answer", "value": r"#[a-z]+"}, result
    ).passed


def test_tool_path_exact():
    result = make_result(tool_calls=("web_search",))
    assert check_property(
        {"property": "tool_path", "value": ["web_search"]}, result
    ).passed
    assert not check_property(
        {"property": "tool_path", "value": ["web_search", "fs_read"]}, result
    ).passed


def test_tool_path_subset():
    result = make_result(tool_calls=("web_search", "fs_read"))
    spec = {"property": "tool_path", "value": ["web_search"], "mode": "subset"}
    assert check_property(spec, result).passed


def test_tool_path_no_flailing():
    result = make_result(tool_calls=("web_search",) * 10)
    spec = {"property": "tool_path", "value": ["web_search"], "mode": "no_flailing"}
    assert not check_property(spec, result).passed


def test_tool_path_unknown_mode_raises():
    result = make_result()
    with pytest.raises(UnknownPropertyError):
        check_property({"property": "tool_path", "value": [], "mode": "bogus"}, result)


def test_max_latency_s():
    result = make_result(latency_s=1.0)
    assert check_property({"property": "max_latency_s", "value": 2.0}, result).passed
    assert not check_property(
        {"property": "max_latency_s", "value": 0.5}, result
    ).passed


def test_max_cost_usd():
    result = make_result(cost_usd=0.1)
    assert check_property({"property": "max_cost_usd", "value": 0.5}, result).passed
    assert not check_property(
        {"property": "max_cost_usd", "value": 0.01}, result
    ).passed


def test_exit_code():
    result = make_result(exit_code=0)
    assert check_property({"property": "exit_code", "value": 0}, result).passed
    assert not check_property({"property": "exit_code", "value": 1}, result).passed


def test_groundedness_ratio_all_grounded():
    result = make_result(
        claims=(Claim("a", evidence_refs=("r1",)), Claim("b", evidence_refs=("r2",)))
    )
    assert check_property(
        {"property": "groundedness_ratio", "min_ratio": 1.0}, result
    ).passed


def test_groundedness_ratio_partial_fails_at_full_threshold():
    result = make_result(
        claims=(Claim("a", evidence_refs=("r1",)), Claim("b", evidence_refs=()))
    )
    outcome = check_property(
        {"property": "groundedness_ratio", "min_ratio": 1.0}, result
    )
    assert not outcome.passed
    assert "1/2" in outcome.detail


def test_groundedness_ratio_partial_passes_at_lower_threshold():
    result = make_result(
        claims=(Claim("a", evidence_refs=("r1",)), Claim("b", evidence_refs=()))
    )
    assert check_property(
        {"property": "groundedness_ratio", "min_ratio": 0.5}, result
    ).passed


def test_groundedness_ratio_no_claims_is_vacuously_true():
    result = make_result(claims=())
    assert check_property(
        {"property": "groundedness_ratio", "min_ratio": 1.0}, result
    ).passed


def test_no_leakage_passes_with_verdict():
    result = make_result(completed_claimed=True, verdict_passed=True)
    assert check_property({"property": "no_leakage"}, result).passed


def test_no_leakage_fails_without_verdict():
    result = make_result(completed_claimed=True, verdict_passed=False)
    assert not check_property({"property": "no_leakage"}, result).passed


def test_no_leakage_fails_with_verdict_none():
    result = make_result(completed_claimed=True, verdict_passed=None)
    assert not check_property({"property": "no_leakage"}, result).passed


def test_no_leakage_passes_when_not_claiming_completion():
    result = make_result(completed_claimed=False, verdict_passed=None)
    assert check_property({"property": "no_leakage"}, result).passed


def test_no_error():
    assert check_property({"property": "no_error"}, make_result(error=None)).passed
    assert not check_property(
        {"property": "no_error"}, make_result(error="boom")
    ).passed


def test_unknown_property_raises():
    with pytest.raises(UnknownPropertyError):
        check_property({"property": "does_not_exist"}, make_result())


def test_dimension_mapping_covers_every_checker():
    for prop in (
        "contains",
        "not_contains",
        "regex",
        "tool_path",
        "max_latency_s",
        "max_cost_usd",
        "exit_code",
        "groundedness_ratio",
        "no_leakage",
        "no_error",
    ):
        assert dimension_for(prop) in {
            "correctness",
            "tool_path_validity",
            "latency",
            "cost",
            "groundedness",
        }


def test_dimension_for_unknown_property_defaults_to_correctness():
    assert dimension_for("something_new") == "correctness"
