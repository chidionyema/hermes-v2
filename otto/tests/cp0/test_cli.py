"""End-to-end CLI integration tests: real subprocess, real files, no mocking of the boundary.

Proves the whole pipeline the task description names: ``otto-eval run`` (here invoked as
``python3 -m otto.evals.cli run``, see ``otto/evals/cli.py`` docstring for why there is no
packaged console script yet) producing a report artefact, then ``otto-eval gate`` reading two
such artefacts and exiting non-zero on regression / zero on pass.

The repository root is derived from ``__file__`` at runtime, never hardcoded (LAW 46).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SUITE_REGRESSION_DIR = Path(__file__).resolve().parent / "fixtures" / "suite_regression"


def _run_cli(
    args: list[str], env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "otto.evals.cli", *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )


def test_run_writes_a_valid_report(tmp_path):
    out_path = tmp_path / "report.json"
    result = _run_cli(
        [
            "run",
            "--suite-dir",
            str(SUITE_REGRESSION_DIR),
            "--agent",
            "otto.tests.cp0.fixtures.fake_agents:agent_pass",
            "--out",
            str(out_path),
        ]
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(out_path.read_text())
    assert report["suite"] == "core"
    assert report["aggregate"]["case_count"] == 1
    assert "content_sha256" in report


def test_gate_passes_on_identical_reports(tmp_path):
    out_a = tmp_path / "a.json"
    out_b = tmp_path / "b.json"
    for out in (out_a, out_b):
        result = _run_cli(
            [
                "run",
                "--suite-dir",
                str(SUITE_REGRESSION_DIR),
                "--agent",
                "otto.tests.cp0.fixtures.fake_agents:agent_pass",
                "--out",
                str(out),
            ]
        )
        assert result.returncode == 0, result.stderr

    gate_result = _run_cli(
        ["gate", "--baseline", str(out_a), "--candidate", str(out_b)]
    )
    assert gate_result.returncode == 0, gate_result.stdout + gate_result.stderr
    assert "PASS" in gate_result.stdout


def test_gate_fails_on_regression(tmp_path):
    baseline_path = tmp_path / "baseline.json"
    candidate_path = tmp_path / "candidate.json"
    assert (
        _run_cli(
            [
                "run",
                "--suite-dir",
                str(SUITE_REGRESSION_DIR),
                "--agent",
                "otto.tests.cp0.fixtures.fake_agents:agent_pass",
                "--out",
                str(baseline_path),
            ]
        ).returncode
        == 0
    )
    assert (
        _run_cli(
            [
                "run",
                "--suite-dir",
                str(SUITE_REGRESSION_DIR),
                "--agent",
                "otto.tests.cp0.fixtures.fake_agents:agent_regressed",
                "--out",
                str(candidate_path),
            ]
        ).returncode
        == 0
    )

    gate_result = _run_cli(
        ["gate", "--baseline", str(baseline_path), "--candidate", str(candidate_path)]
    )
    assert gate_result.returncode != 0
    assert "FAIL" in gate_result.stdout


def test_gate_refuses_a_malformed_candidate_report(tmp_path):
    baseline_path = tmp_path / "baseline.json"
    assert (
        _run_cli(
            [
                "run",
                "--suite-dir",
                str(SUITE_REGRESSION_DIR),
                "--agent",
                "otto.tests.cp0.fixtures.fake_agents:agent_pass",
                "--out",
                str(baseline_path),
            ]
        ).returncode
        == 0
    )
    malformed_path = tmp_path / "malformed.json"
    malformed_path.write_text(json.dumps({"schema_version": 1, "suite": "core"}))

    gate_result = _run_cli(
        ["gate", "--baseline", str(baseline_path), "--candidate", str(malformed_path)]
    )
    assert gate_result.returncode != 0
    assert "REFUSED" in gate_result.stderr


def test_run_enforces_case_timeout_end_to_end(tmp_path):
    out_path = tmp_path / "report.json"
    timeout_case_dir = tmp_path / "timeout_suite"
    timeout_case_dir.mkdir()
    (timeout_case_dir / "case.yaml").write_text(
        "id: cli-timeout-1\n"
        "tier: T0\n"
        "task_class: research\n"
        "input: {task: x}\n"
        "timeout_s: 0.2\n"
        "expect: [{property: exit_code, value: 0}]\n"
    )
    result = _run_cli(
        [
            "run",
            "--suite-dir",
            str(timeout_case_dir),
            "--agent",
            "otto.tests.cp0.fixtures.fake_agents:agent_timeout",
            "--out",
            str(out_path),
        ]
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(out_path.read_text())
    case = report["cases"][0]
    assert case["timed_out"] is True
    assert case["score"] == 0.0


def test_run_without_out_or_env_var_fails_loudly():
    env = {k: v for k, v in os.environ.items() if k != "OTTO_EVAL_REPORT_PATH"}
    result = _run_cli(
        [
            "run",
            "--suite-dir",
            str(SUITE_REGRESSION_DIR),
            "--agent",
            "otto.tests.cp0.fixtures.fake_agents:agent_pass",
        ],
        env=env,
    )
    assert result.returncode != 0
