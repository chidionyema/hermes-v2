"""Regression: grounding tokens compare under casefold, not lower.

Independent verifier probe (crew#768 hardening wave): ``str.lower`` does
not fold German sharp s or Turkish dotless-i forms, so a claim and its
evidence spelling the same word with different Unicode case rules held
different token sets — two visually identical words graded a claim
differently, and a token could alias past the overlap check.
``str.casefold`` folds them to the same token.
"""

from __future__ import annotations

from otto.router.config import RouterConfig
from otto.router.contract import Claim
from otto.router.grounding import GroundingCheck


def _checker() -> GroundingCheck:
    return GroundingCheck(config=RouterConfig())


def test_sharp_s_and_double_s_spellings_are_one_token() -> None:
    # "STRASSE".casefold() == "straße".casefold() == "strasse";
    # under .lower() the sharp s survives and the two spellings are
    # different tokens, so identical words grade as zero overlap.
    checker = _checker()
    assert checker.supports("STRASSE", "straße") is True
    assert checker.supports("straße", "STRASSE") is True


def test_casefold_equal_evidence_grounds_the_claim() -> None:
    # The claim is the single word whose spellings only casefold makes
    # equal, so the grade turns entirely on the fold: zero overlap under
    # .lower(), full overlap under .casefold().
    checker = _checker()
    claim = Claim(text="STRASSE", evidence_refs=("ev-1",), confidence="high")
    evidence = {"ev-1": "bericht: die straße wurde gesperrt am montag"}
    assert checker.is_grounded(claim, evidence) is True
