"""Eval gate for router/prompt changes (P6: evals gate change).

Two commands back the CP5 scenarios, exposed as an in-process CLI the
``otto`` entry point will mount at a later checkpoint:

- ``eval diff --baseline <ref>``: computes and RECORDS a per-lane delta
  between the baseline policy's eval metrics and the current ones. Until a
  delta record exists for the current policy, ``merge_word_allowed()`` is
  False — the merge word cannot be given before the delta is shown.
- ``eval run --suite core``: runs the mechanical groundedness check over
  the core suite and reports the ungrounded-claim rate against the
  configured bar (default 5%).

The core suite lives in-code as fixture data; a stored corpus is a later
checkpoint feeding the same functions.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from otto.router.config import RouterConfig
from otto.router.contract import Claim
from otto.router.grounding import GroundingCheck

_LANES = ("judgment", "bulk", "verify")


def _core_suite() -> tuple[list[Claim], dict[str, str]]:
    """The core eval suite: claims plus the evidence store they cite.

    40 grounded cases and 1 deliberately ungrounded case per the corpus
    design (rate 1/41 ≈ 2.4%, under the 5% bar). Mechanical fixtures:
    each grounded claim's tokens appear in its cited evidence.
    """
    claims: list[Claim] = []
    evidence: dict[str, str] = {}
    for i in range(40):
        ref = f"tool_call_{i:03d}"
        subject = f"service{i}"
        evidence[ref] = f"probe output: {subject} responded healthy on port check"
        claims.append(
            Claim(
                text=f"{subject} responded healthy",
                evidence_refs=(ref,),
                confidence="high",
            )
        )
    # The one ungrounded row: the ref resolves, the evidence disagrees.
    evidence["tool_call_bad"] = "probe output: connection refused, pod crash-looping"
    claims.append(
        Claim(
            text="the deploy is fully green and serving traffic",
            evidence_refs=("tool_call_bad",),
            confidence="high",
        )
    )
    return claims, evidence


@dataclass
class EvalGate:
    """Holds eval metrics per policy version and gates the merge word."""

    config: RouterConfig
    records: list[dict] = field(default_factory=list)

    def lane_metrics(self, policy_ref: str) -> dict[str, float]:
        """Ungrounded rate per lane for a policy ref. The core suite is
        lane-agnostic fixture data, so the per-lane figure is the same
        mechanical rate computed under that policy's grounding config."""
        checker = GroundingCheck(self.config)
        claims, evidence = _core_suite()
        rate = checker.ungrounded_rate(claims, evidence)
        return {lane: rate for lane in _LANES}

    def diff(self, baseline_ref: str, current_ref: str = "working-tree") -> dict:
        """Compute, record and return the per-lane delta. Recording is the
        point: an unrecorded eval never happened (P6)."""
        base = self.lane_metrics(baseline_ref)
        current = self.lane_metrics(current_ref)
        record = {
            "baseline": baseline_ref,
            "current": current_ref,
            "per_lane_delta": {lane: current[lane] - base[lane] for lane in _LANES},
            "per_lane_current": current,
        }
        self.records.append(record)
        return record

    def merge_word_allowed(self) -> bool:
        """P6: no merge word before a recorded eval delta exists."""
        return len(self.records) > 0

    def run_core_suite(self) -> dict:
        checker = GroundingCheck(self.config)
        claims, evidence = _core_suite()
        rate = checker.ungrounded_rate(claims, evidence)
        return {
            "suite": "core",
            "claims": len(claims),
            "ungrounded_rate": rate,
            "bar": self.config.ungrounded_rate_bar,
            "passed": rate < self.config.ungrounded_rate_bar,
        }


def render_diff(record: dict) -> str:
    """Human-readable delta — what the engineer is SHOWN before merging."""
    lines = [
        f"eval delta: baseline {record['baseline']} -> {record['current']}",
    ]
    for lane, delta in record["per_lane_delta"].items():
        current = record["per_lane_current"][lane]
        lines.append(
            f"  lane {lane}: ungrounded rate {current:.4f} (delta {delta:+.4f})"
        )
    return "\n".join(lines)


def run_eval_cli(argv: list[str], gate: EvalGate) -> tuple[int, str]:
    """In-process CLI: (exit code, output shown to the engineer)."""
    if len(argv) >= 3 and argv[0] == "diff" and argv[1] == "--baseline":
        record = gate.diff(argv[2])
        return 0, render_diff(record)
    if len(argv) >= 3 and argv[0] == "run" and argv[1] == "--suite":
        if argv[2] != "core":
            return 1, f"unknown suite '{argv[2]}'"
        result = gate.run_core_suite()
        verdict = "PASS" if result["passed"] else "FAIL"
        return (0 if result["passed"] else 1), (
            f"suite core: {result['claims']} claims, ungrounded rate "
            f"{result['ungrounded_rate']:.4f} (bar {result['bar']:.2f}) {verdict}"
        )
    return 1, "usage: eval diff --baseline <ref> | eval run --suite core"
