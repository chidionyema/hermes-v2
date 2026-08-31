"""Otto CP0 eval harness (crew#768).

Spec of record: crew repo, branch ``spec/otto-platform-v1``,
``docs/founder/2026-08-31-otto-platform-build-spec-v1.md`` section 11
("Eval harness — built first, Phase 0") and constitution invariant P6
("Evals gate change"). Feature file:
``docs/specs/otto-platform-v1/features/cp1_spine_and_measurement.feature``.

DSPy note (R64 — all platform prompt work must be DSPy): this package
contains **no prompts and makes no model calls**. Scoring in v0 is entirely
property-based (substring/regex/tool-path/latency/cost/groundedness-ratio
checks over structured ``EvalResult`` objects the agent under test returns).
There is no LLM-as-judge in v0 (spec section 11 leaves that door open for a
later version). Because there is no prompt anywhere in this module tree,
R64 does not apply to it; the day an LLM-as-judge dimension is added here,
that judge prompt must be built with DSPy, not a hand string, and this
docstring must be updated to say so honestly.

Modules:
    models   -- EvalCase, EvalResult, Claim: the data shapes.
    scoring  -- pure, deterministic property checkers. No model calls.
    runner   -- runs a suite against a pluggable agent-under-test callable.
    report   -- deterministic, sha256-stamped JSON report artefact.
    gate     -- baseline-vs-candidate regression gate, configurable thresholds.
    cli      -- ``python3 -m otto.evals.cli {run,gate}`` entry point.
"""
