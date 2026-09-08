"""Deterministic, sha256-stamped report artefact.

"Deterministic" here means: the same suite result always serializes to the
same JSON bytes (sorted keys, fixed separators, no locale-dependent float
formatting) and therefore always hashes to the same sha256. Every wall-clock
field -- the top-level ``generated_at`` timestamp AND each case's
``elapsed_s`` runtime -- is metadata, excluded from the hashed content, so
re-running an unchanged suite twice produces two reports whose
``content_sha256`` is identical even though real time elapsed differently
between the two runs. The fields still appear in the report on disk for a
human or a latency dashboard to read; they are only invisible to the hash.
That identity is exactly what the regression gate (``otto.evals.gate``) and
the "report is proof, not narration" requirement need: two runs of the same
candidate must be provably the same artefact, not merely close.

The report path is never hardcoded (LAW 46): callers pass a path explicitly,
or the CLI reads it from the ``OTTO_EVAL_REPORT_PATH`` environment variable.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from otto.evals.runner import SuiteReport

REPORT_SCHEMA_VERSION = 1
REPORT_PATH_ENV_VAR = "OTTO_EVAL_REPORT_PATH"


class MalformedReportError(ValueError):
    """Raised when a report on disk is not a well-formed, correctly-stamped artefact.

    The gate fails closed on this: a report that cannot be trusted blocks
    the merge, it never passes by default.
    """


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _suite_report_to_content(suite_report: SuiteReport) -> dict[str, Any]:
    cases = []
    for case in sorted(suite_report.cases, key=lambda c: c.case_id):
        cases.append(
            {
                "case_id": case.case_id,
                "tier": case.tier,
                "task_class": case.task_class,
                "tags": sorted(case.tags),
                "passed": case.passed,
                "score": round(case.score, 6),
                "timed_out": case.timed_out,
                "elapsed_s": round(case.elapsed_s, 6),
                "agent_error": case.agent_error,
                "outcomes": [
                    {"property": o.property, "passed": o.passed, "detail": o.detail}
                    for o in case.outcomes
                ],
            }
        )
    aggregate = {
        "suite_score": round(suite_report.suite_score, 6),
        "per_dimension_score": {
            k: round(v, 6) for k, v in suite_report.per_dimension_score.items()
        },
        "leakage_rate": round(suite_report.leakage_rate, 6),
        "case_count": len(suite_report.cases),
    }
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "suite": suite_report.suite,
        "cases": cases,
        "aggregate": aggregate,
    }


# Per-case fields that measure real wall-clock time. These stay in the report
# for humans and dashboards but are stripped before hashing -- a field whose
# value depends on how fast the machine happened to be that second must never
# make two logically-identical runs hash differently.
WALL_CLOCK_CASE_FIELDS = ("elapsed_s",)


def _redact_wall_clock(content: dict[str, Any]) -> dict[str, Any]:
    redacted = dict(content)
    redacted["cases"] = [
        {k: v for k, v in case.items() if k not in WALL_CLOCK_CASE_FIELDS}
        for case in content["cases"]
    ]
    return redacted


def content_sha256(content: dict[str, Any]) -> str:
    """Hash the content, with all wall-clock fields (see WALL_CLOCK_CASE_FIELDS) redacted first."""
    return hashlib.sha256(
        _canonical_json(_redact_wall_clock(content)).encode("utf-8")
    ).hexdigest()


def build_report_dict(
    suite_report: SuiteReport, generated_at: str | None = None
) -> dict[str, Any]:
    content = _suite_report_to_content(suite_report)
    return {
        **content,
        "content_sha256": content_sha256(content),
        "generated_at": generated_at
        or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def write_report(
    suite_report: SuiteReport, path: Path, generated_at: str | None = None
) -> dict[str, Any]:
    report = build_report_dict(suite_report, generated_at=generated_at)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_canonical_json(report) + "\n", encoding="utf-8")
    return report


REQUIRED_REPORT_FIELDS = (
    "schema_version",
    "suite",
    "cases",
    "aggregate",
    "content_sha256",
    "generated_at",
)


def validate_report_dict(report: Any, source: str = "<dict>") -> dict[str, Any]:
    """Fail-closed validation: raise MalformedReportError on anything wrong.

    Checked, in order: it is a mapping; every required field is present;
    the schema version is one we understand; and the stored
    ``content_sha256`` matches a fresh hash of the content -- a report that
    was hand-edited or truncated is refused, never silently accepted.
    """
    if not isinstance(report, dict):
        raise MalformedReportError(
            f"{source}: report must be a mapping, got {type(report).__name__}"
        )
    missing = [f for f in REQUIRED_REPORT_FIELDS if f not in report]
    if missing:
        raise MalformedReportError(
            f"{source}: missing required field(s): {', '.join(missing)}"
        )
    if report["schema_version"] != REPORT_SCHEMA_VERSION:
        raise MalformedReportError(
            f"{source}: unsupported schema_version {report['schema_version']!r}, expected {REPORT_SCHEMA_VERSION}"
        )
    if not isinstance(report["cases"], list):
        raise MalformedReportError(f"{source}: 'cases' must be a list")
    if not isinstance(report["aggregate"], dict):
        raise MalformedReportError(f"{source}: 'aggregate' must be a mapping")
    content = {k: report[k] for k in ("schema_version", "suite", "cases", "aggregate")}
    expected_sha = content_sha256(content)
    if report["content_sha256"] != expected_sha:
        raise MalformedReportError(
            f"{source}: content_sha256 mismatch (stored {report['content_sha256']!r}, "
            f"computed {expected_sha!r}) -- report was edited or corrupted after generation"
        )
    return report


def read_report(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise MalformedReportError(f"{path}: could not read: {exc}") from exc
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise MalformedReportError(f"{path}: not valid JSON: {exc}") from exc
    return validate_report_dict(raw, source=str(path))


def resolve_report_path(cli_value: str | None) -> Path:
    """CLI flag wins; otherwise OTTO_EVAL_REPORT_PATH; otherwise an explicit error.

    Never a hardcoded default path (LAW 46: a file never names where the
    checkout or the machine lives).
    """
    value = cli_value or os.environ.get(REPORT_PATH_ENV_VAR)
    if not value:
        raise ValueError(
            f"no report path given: pass --out or set {REPORT_PATH_ENV_VAR}"
        )
    return Path(value)
