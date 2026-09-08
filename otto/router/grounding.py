"""Mechanical groundedness check (spec section 5 acceptance: rate < 5%).

A claim is grounded only when BOTH hold: every evidence ref resolves to a
real tool result, AND the resolved evidence actually supports the claim's
text. A ref that resolves but does not support the claim counts as
UNGROUNDED — existence of a ref is never proof (the feature's edge case,
and the estate's silent-green rule).

"Supports" is mechanical, not semantic: the configured fraction of the
claim's significant tokens must appear in the referenced evidence text.
Deterministic, model-free, and tunable via config — never a model judging
its own homework.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from otto.router.config import RouterConfig
from otto.router.contract import Claim

_WORD = re.compile(r"[a-z0-9]+")
# Tokens too common to carry evidence weight; kept tiny and boring on purpose.
_STOPWORDS = frozenset(
    "a an and are as at be by for from has in is it of on or that the this to was with".split()
)


def _significant_tokens(text: str) -> set[str]:
    # casefold, not lower: tokens that differ only by Unicode case rules
    # (German ss/SS, Turkish dotless-i forms) must compare equal, or two
    # spellings of the same word grade a claim differently.
    return {t for t in _WORD.findall(text.casefold()) if t not in _STOPWORDS}


@dataclass(frozen=True)
class GroundingCheck:
    """Grades claims against a store of resolved evidence texts."""

    config: RouterConfig

    def supports(self, claim_text: str, evidence_text: str) -> bool:
        claim_tokens = _significant_tokens(claim_text)
        if not claim_tokens:
            return False
        evidence_tokens = _significant_tokens(evidence_text)
        overlap = len(claim_tokens & evidence_tokens) / len(claim_tokens)
        return overlap >= self.config.grounding_min_overlap

    def is_grounded(self, claim: Claim, evidence_store: dict[str, str]) -> bool:
        """Grounded = at least one ref, every ref resolves, and at least one
        resolved evidence text mechanically supports the claim."""
        if not claim.evidence_refs:
            return False
        texts = []
        for ref in claim.evidence_refs:
            if ref not in evidence_store:
                return False  # a dangling ref is ungrounded, not ignorable
            texts.append(evidence_store[ref])
        return any(self.supports(claim.text, t) for t in texts)

    def ungrounded_rate(
        self, claims: list[Claim], evidence_store: dict[str, str]
    ) -> float:
        """Fraction of claims that fail the mechanical check. Empty input is
        0.0 by definition (no claims, nothing ungrounded)."""
        if not claims:
            return 0.0
        bad = sum(1 for c in claims if not self.is_grounded(c, evidence_store))
        return bad / len(claims)
