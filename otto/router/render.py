"""Unverified-claim rendering — a gateway rule, never a model instruction.

Spec section 5: claims with empty ``evidence_refs`` render prefixed with
the unverified marker, on every channel. Which surface the lines end up
on is not this module's business — the marker is a property of the claim,
not of the delivery. The rule reads ONLY the structural
fact (does the claim carry evidence refs, does a verdict exist) — nothing
the model wrote in the claim text, and no confidence value the model chose,
can suppress the marker. P1 in rendering form: output is unverified until
a verdict says otherwise.
"""

from __future__ import annotations

from otto.router.contract import RouterResponse, VerificationStatus

#: The marker the founder's spec fixes for an unverified claim.
UNVERIFIED_PREFIX = "⚠ unverified: "


def render_claim(text: str, *, has_evidence: bool, verified: bool) -> str:
    """Render one claim line. The inputs are structural facts computed by
    the gateway; the model's own words are payload, never policy."""
    if verified and has_evidence:
        return text
    return f"{UNVERIFIED_PREFIX}{text}"


def render_claims(response: RouterResponse) -> list[str]:
    """Render every claim of a response, ready for any surface to deliver.

    A claim renders unmarked only when the response carries an external
    VERIFIED status AND the claim itself has evidence refs. Everything
    else — empty refs, or no verdict yet — gets the explicit marker,
    whatever the model claimed about itself.
    """
    verified = response.verification is VerificationStatus.VERIFIED
    return [
        render_claim(c.text, has_evidence=c.has_evidence, verified=verified)
        for c in response.claims
    ]
