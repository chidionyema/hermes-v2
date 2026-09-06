"""A model's spelling of a confidence is not a different claim (founder
2026-09-05: a fallback model wrote "medium" and every answer was refused
with ``claims[1].confidence not one of ('high', 'med', 'low')``)."""

import json

import pytest

from otto.router.contract import (
    MalformedProviderOutput,
    normalise_confidence,
    normalise_provider_output,
)


@pytest.mark.parametrize(
    ("spelling", "meaning"),
    [
        ("high", "high"),
        ("High", "high"),
        (" MED ", "med"),
        ("medium", "med"),
        ("Moderate", "med"),
        ("low", "low"),
        (0.9, "high"),
        (0.5, "med"),
        (0.1, "low"),
        (1, "high"),
    ],
)
def test_a_spelling_of_a_legal_value_reads_as_that_value(spelling, meaning) -> None:
    assert normalise_confidence(spelling) == meaning


@pytest.mark.parametrize("value", ["very high", "", None, 1.5, -0.1, True, [], {}])
def test_a_value_outside_the_table_is_refused_not_rounded(value) -> None:
    assert normalise_confidence(value) is None


def test_the_contract_reads_medium_as_med_and_still_refuses_nonsense() -> None:
    def output(confidence):
        return json.dumps(
            {
                "answer": "x",
                "claims": [
                    {"text": "c", "evidence_refs": [], "confidence": confidence}
                ],
                "proposed_actions": [],
                "unknowns": [],
            }
        )

    kwargs = dict(
        lane="judgment",
        model="m",
        task_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
        cost_usd=0.0,
        tokens=1,
    )
    response = normalise_provider_output(output("Medium"), **kwargs)
    assert response.claims[0].confidence == "med"
    with pytest.raises(MalformedProviderOutput, match=r"claims\[0\]\.confidence"):
        normalise_provider_output(output("certain"), **kwargs)
