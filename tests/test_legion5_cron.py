"""Legion 5 pins: cron-run supervision wrapper + install-cron validation.

Pins the production gates the Architect legion must hold: supervision (no
overlap), hard wall-clock budget, health records for the dead-man's switch,
and .jobs validation that fails BEFORE anything is created.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
CRON_RUN = REPO / "bin" / "cron-run"
INSTALL_CRON = REPO / "bin" / "install-cron.py"


def _load_install_cron():
    spec = importlib.util.spec_from_file_location("install_cron", INSTALL_CRON)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_cron(args, home):
    env = dict(os.environ, HERMES_HOME=str(home))
    return subprocess.run(
        [sys.executable, str(CRON_RUN), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def _health(home, name):
    p = home / "state" / "cron-health" / f"{name}.json"
    return json.loads(p.read_text()) if p.exists() else None


# --------------------------------------------------------------------------- #
# cron-run: health + exit passthrough
# --------------------------------------------------------------------------- #
def test_success_writes_health_and_passes_exit(tmp_path) -> None:
    r = _run_cron(["job-a", "--", "true"], tmp_path)
    assert r.returncode == 0
    h = _health(tmp_path, "job-a")
    assert h["status"] == "ok" and h["exit"] == 0
    assert isinstance(h["duration_ms"], float)
    assert h["last_start"] and h["last_end"]


def test_failure_exit_code_passthrough_and_metered(tmp_path) -> None:
    r = _run_cron(["job-b", "--", "sh", "-c", "exit 3"], tmp_path)
    assert r.returncode == 3
    h = _health(tmp_path, "job-b")
    assert h["status"] == "failed" and h["exit"] == 3


def test_timeout_kills_process_group_and_meters(tmp_path) -> None:
    r = _run_cron(["job-c", "--timeout", "0.2", "--", "sleep", "10"], tmp_path)
    assert r.returncode == 124
    h = _health(tmp_path, "job-c")
    assert h["status"] == "timeout" and h["timeout_s"] == 0.2


def test_timeout_clamped_to_ceiling(tmp_path) -> None:
    r = _run_cron(["job-d", "--timeout", "999999", "--", "true"], tmp_path)
    assert r.returncode == 0
    assert _health(tmp_path, "job-d")["timeout_s"] == 3600.0


def test_overlap_is_refused_and_metered(tmp_path) -> None:
    slow = subprocess.Popen(
        [sys.executable, str(CRON_RUN), "job-e", "--", "sleep", "2"],
        env=dict(os.environ, HERMES_HOME=str(tmp_path)),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(0.3)  # let the first run take the lock
    r = _run_cron(["job-e", "--", "true"], tmp_path)
    assert r.returncode == 75
    assert "overlap" in r.stderr
    # the refusal is metered while the first run still holds the lock
    h = _health(tmp_path, "job-e")
    assert h["status"] == "overlap-refused" and h["exit"] == 75
    slow.wait()
    # after the slow run finishes, health flips to its own outcome
    assert _health(tmp_path, "job-e")["status"] == "ok"


def test_bad_invocation_exits_cleanly(tmp_path) -> None:
    assert _run_cron([], tmp_path).returncode != 0
    assert _run_cron(["job-f"], tmp_path).returncode != 0  # no -- cmd
    assert _run_cron(["bad name", "--", "true"], tmp_path).returncode != 0
    assert _run_cron(["j", "--timeout", "0", "--", "true"], tmp_path).returncode != 0


# --------------------------------------------------------------------------- #
# install-cron: validation fails loudly BEFORE anything is created
# --------------------------------------------------------------------------- #
def test_valid_schedule_accepts_standard_forms() -> None:
    ic = _load_install_cron()
    for good in ("0 3 * * *", "*/15 * * * *", "0 4 * * 0", "30 2 1 jan sun", "@daily"):
        assert ic.valid_schedule(good), good
    for bad in ("0 3 * *", "0 3 * * * *", "", "every day", "0 3 * * * extra"):
        assert not ic.valid_schedule(bad), bad


def _write_jobs(tmp_path, lines) -> Path:
    p = tmp_path / "t.jobs"
    p.write_text("\n".join(lines) + "\n")
    return p


def test_load_rejects_bad_schedule(tmp_path) -> None:
    ic = _load_install_cron()
    p = _write_jobs(tmp_path, ['{"name": "a", "schedule": "every day", "prompt": "x"}'])
    with pytest.raises(SystemExit, match="not a 5-field cron"):
        ic.load(str(p))


def test_load_rejects_duplicate_names(tmp_path) -> None:
    ic = _load_install_cron()
    p = _write_jobs(
        tmp_path,
        [
            '{"name": "a", "schedule": "0 3 * * *", "prompt": "x"}',
            '{"name": "a", "schedule": "0 4 * * *", "prompt": "y"}',
        ],
    )
    with pytest.raises(SystemExit, match="duplicates job name"):
        ic.load(str(p))


def test_load_rejects_prompt_and_script_together(tmp_path) -> None:
    ic = _load_install_cron()
    p = _write_jobs(
        tmp_path,
        ['{"name": "a", "schedule": "0 3 * * *", "prompt": "x", "script": "s.sh"}'],
    )
    with pytest.raises(SystemExit, match="both"):
        ic.load(str(p))


def test_load_rejects_job_with_no_action(tmp_path) -> None:
    ic = _load_install_cron()
    p = _write_jobs(tmp_path, ['{"name": "a", "schedule": "0 3 * * *"}'])
    with pytest.raises(SystemExit, match="neither"):
        ic.load(str(p))


def test_load_rejects_no_agent_without_script(tmp_path) -> None:
    ic = _load_install_cron()
    p = _write_jobs(
        tmp_path,
        ['{"name": "a", "schedule": "0 3 * * *", "prompt": "x", "no_agent": true}'],
    )
    with pytest.raises(SystemExit, match="no_agent"):
        ic.load(str(p))


def test_load_accepts_well_formed_and_skips_comments(tmp_path) -> None:
    ic = _load_install_cron()
    p = _write_jobs(
        tmp_path,
        [
            "# a comment",
            "",
            '{"name": "a", "schedule": "0 3 * * *", "prompt": "x"}',
            '{"name": "b", "schedule": "@daily", "script": "b.sh", "no_agent": true}',
        ],
    )
    jobs = ic.load(str(p))
    assert [j["name"] for j in jobs] == ["a", "b"]


def test_feature_flag_without_name_is_clean_error(tmp_path) -> None:
    env = dict(os.environ)
    r = subprocess.run(
        [sys.executable, str(INSTALL_CRON), "cron/x.jobs", "--feature"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert r.returncode != 0
    assert "--feature needs a lane name" in (r.stderr + r.stdout)
