from __future__ import annotations

import json

import pytest

from otto.evals.models import EvalCase
from otto.evals.report import (
    MalformedReportError,
    REPORT_PATH_ENV_VAR,
    build_report_dict,
    read_report,
    resolve_report_path,
    write_report,
)
from otto.evals.runner import run_cases
from otto.tests.cp0.fixtures.fake_agents import agent_pass

CASE = EvalCase(
    id="r-1",
    tier="T0",
    task_class="research",
    input={"task": "x"},
    timeout_s=1.0,
    expect=({"property": "exit_code", "value": 0},),
)


def test_report_is_deterministic_given_same_content():
    suite_report = run_cases(agent_pass, [CASE], suite="core")
    r1 = build_report_dict(suite_report, generated_at="2026-01-01T00:00:00Z")
    r2 = build_report_dict(suite_report, generated_at="2099-12-31T23:59:59Z")
    # different wall-clock timestamps must not change the content hash
    assert r1["content_sha256"] == r2["content_sha256"]


def test_write_then_read_round_trips(tmp_path):
    suite_report = run_cases(agent_pass, [CASE], suite="core")
    path = tmp_path / "report.json"
    written = write_report(suite_report, path)
    read_back = read_report(path)
    assert read_back == written


def test_written_report_is_valid_json_on_disk(tmp_path):
    suite_report = run_cases(agent_pass, [CASE], suite="core")
    path = tmp_path / "report.json"
    write_report(suite_report, path)
    # deterministic serialization: parses, and every required key present.
    parsed = json.loads(path.read_text())
    assert parsed["schema_version"] == 1
    assert parsed["suite"] == "core"


def test_malformed_report_missing_field_refused(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"schema_version": 1, "suite": "core"}))
    with pytest.raises(MalformedReportError):
        read_report(path)


def test_malformed_report_bad_json_refused(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json")
    with pytest.raises(MalformedReportError):
        read_report(path)


def test_malformed_report_missing_file_refused(tmp_path):
    with pytest.raises(MalformedReportError):
        read_report(tmp_path / "does-not-exist.json")


def test_tampered_content_sha_is_refused(tmp_path):
    suite_report = run_cases(agent_pass, [CASE], suite="core")
    path = tmp_path / "report.json"
    write_report(suite_report, path)
    report = json.loads(path.read_text())
    report["aggregate"]["suite_score"] = 999.0  # tamper with content, leave stale sha
    path.write_text(json.dumps(report))
    with pytest.raises(MalformedReportError, match="content_sha256 mismatch"):
        read_report(path)


def test_unsupported_schema_version_refused(tmp_path):
    suite_report = run_cases(agent_pass, [CASE], suite="core")
    path = tmp_path / "report.json"
    written = write_report(suite_report, path)
    written["schema_version"] = 999
    path.write_text(json.dumps(written))
    with pytest.raises(MalformedReportError):
        read_report(path)


def test_resolve_report_path_prefers_cli_flag(monkeypatch, tmp_path):
    monkeypatch.setenv(REPORT_PATH_ENV_VAR, str(tmp_path / "env-path.json"))
    resolved = resolve_report_path(str(tmp_path / "cli-path.json"))
    assert resolved.name == "cli-path.json"


def test_resolve_report_path_falls_back_to_env(monkeypatch, tmp_path):
    monkeypatch.setenv(REPORT_PATH_ENV_VAR, str(tmp_path / "env-path.json"))
    resolved = resolve_report_path(None)
    assert resolved.name == "env-path.json"


def test_resolve_report_path_raises_when_neither_given(monkeypatch):
    monkeypatch.delenv(REPORT_PATH_ENV_VAR, raising=False)
    with pytest.raises(ValueError):
        resolve_report_path(None)
