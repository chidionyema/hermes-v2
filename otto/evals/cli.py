"""``otto-eval`` CLI: run a suite, or gate a candidate report against a baseline.

There is no packaged console-script entry point in this checkpoint (CP0 is
scoped to ``otto/evals/`` and ``otto/tests/cp0/`` only, and adding a
``pyproject.toml``/setuptools entry point is a repo-root change outside that
scope). Invoke it as a module instead:

    python3 -m otto.evals.cli run --suite-dir <dir> --agent module:callable --out <path>
    python3 -m otto.evals.cli gate --baseline <path> --candidate <path> [--config <path>]

Registering the ``otto-eval`` console script is tracked as a gap for the
phase that introduces the repo's real packaging file.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

from otto.evals.gate import compare, load_thresholds
from otto.evals.report import (
    MalformedReportError,
    read_report,
    resolve_report_path,
    write_report,
)
from otto.evals.runner import run_suite_dir


def _load_agent(spec: str):
    """Load 'module.path:callable_name' -> the callable."""
    if ":" not in spec:
        raise ValueError(f"--agent must be 'module.path:callable_name', got {spec!r}")
    module_name, attr = spec.split(":", 1)
    module = importlib.import_module(module_name)
    agent = getattr(module, attr)
    if not callable(agent):
        raise ValueError(f"{spec!r} is not callable")
    return agent


def _cmd_run(args: argparse.Namespace) -> int:
    agent = _load_agent(args.agent)
    suite_report = run_suite_dir(agent, Path(args.suite_dir), suite=args.suite)
    out_path = resolve_report_path(args.out)
    report = write_report(suite_report, out_path)
    print(f"wrote report: {out_path} (content_sha256={report['content_sha256']})")
    print(
        f"suite_score={report['aggregate']['suite_score']} case_count={report['aggregate']['case_count']}"
    )
    return 0


def _cmd_gate(args: argparse.Namespace) -> int:
    try:
        baseline = read_report(Path(args.baseline))
        candidate = read_report(Path(args.candidate))
    except MalformedReportError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    thresholds = load_thresholds(args.config)
    result = compare(baseline, candidate, thresholds)
    print(result.summary())
    return 0 if result.passed else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="otto-eval")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="run a suite against an agent under test")
    run_p.add_argument(
        "--suite-dir", required=True, help="directory of eval case files"
    )
    run_p.add_argument(
        "--suite", default="core", help="suite name recorded in the report"
    )
    run_p.add_argument("--agent", required=True, help="'module.path:callable_name'")
    run_p.add_argument(
        "--out", default=None, help="report path (else OTTO_EVAL_REPORT_PATH)"
    )
    run_p.set_defaults(func=_cmd_run)

    gate_p = sub.add_parser("gate", help="fail if candidate regressed vs baseline")
    gate_p.add_argument("--baseline", required=True)
    gate_p.add_argument("--candidate", required=True)
    gate_p.add_argument(
        "--config",
        default=None,
        help="threshold config path (else OTTO_EVAL_GATE_CONFIG, else defaults)",
    )
    gate_p.set_defaults(func=_cmd_gate)

    return parser


def main(argv: list[str] | None = None) -> int:
    from otto.evals import boot

    boot()  # W2 (crew#768): instrument or refuse to run dark
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
