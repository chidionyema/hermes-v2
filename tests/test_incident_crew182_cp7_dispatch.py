"""crew#182 CP7: the dispatcher claims an agent-go issue and never an icebox one.
Rung 4, incident test. The failing case it guards: the old work-agent-go was a
model prompt that could claim anything it liked and read the wrong repo. This
runs scripts/dispatch-agent-go.py --drill against a fixture board and a throwaway
git repo, both ways in one run.
"""
import json
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "dispatch-agent-go.py"


def _git(cwd, *a):
    subprocess.run(["git", "-C", str(cwd), *a], check=True, capture_output=True)


def _run(d):
    return subprocess.run([sys.executable, str(SCRIPT), "--drill", str(d), "--runtime-cmd", "sh -c 'echo ran > ran.txt'"],
                          capture_output=True, text=True)


def test_agent_go_is_claimed_and_icebox_never(tmp_path):
    co = tmp_path / "checkout"
    co.mkdir()
    _git(co, "init", "-q", "-b", "main")
    _git(co, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "base")
    (tmp_path / "issues.json").write_text(json.dumps([
        {"number": 7, "title": "icebox idea", "body": "", "labels": [{"name": "agent-go"}, {"name": "icebox"}]},
        {"number": 8, "title": "already claimed", "body": "", "labels": [{"name": "agent-go"}, {"name": "in-progress"}]},
        {"number": 9, "title": "do it", "body": "Repo: drill/checkout", "labels": [{"name": "agent-go"}]},
    ]))
    r = _run(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "CLAIMED #9" in r.stdout
    assert "#7" not in r.stdout and "#8" not in r.stdout
    wts = subprocess.run(["git", "-C", str(co), "worktree", "list"], capture_output=True, text=True).stdout
    assert "[agent-go/9]" in wts and "agent-go/7" not in wts and "agent-go/8" not in wts
    assert (co / ".worktrees" / "agent-go-9" / "ran.txt").read_text().strip() == "ran"
    # a second tick claims nothing new: #9 is now in-progress, #7 and #8 still never
    r2 = _run(tmp_path)
    assert r2.returncode == 0 and r2.stdout.strip() == "", r2.stdout


def test_missing_runtime_leaves_issue_unclaimed(tmp_path):
    co = tmp_path / "checkout"
    co.mkdir()
    _git(co, "init", "-q", "-b", "main")
    _git(co, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "base")
    (tmp_path / "issues.json").write_text(json.dumps([{"number": 1, "title": "x", "body": "", "labels": [{"name": "agent-go"}]}]))
    r = subprocess.run([sys.executable, str(SCRIPT), "--drill", str(tmp_path), "--runtime-cmd", "no-such-runtime-xyz"],
                       capture_output=True, text=True)
    assert r.returncode == 1 and "FAIL #1" in r.stdout
    assert not (tmp_path / "claims.json").exists()


def test_runtime_exiting_nonzero_leaves_issue_unclaimed(tmp_path):
    # code-c1 on hermes-v2#10: `--runtime-cmd false` printed CLAIMED and wrote claims.json.
    co = tmp_path / "checkout"
    co.mkdir()
    _git(co, "init", "-q", "-b", "main")
    _git(co, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "base")
    (tmp_path / "issues.json").write_text(json.dumps([{"number": 1, "title": "x", "body": "", "labels": [{"name": "agent-go"}]}]))
    r = subprocess.run([sys.executable, str(SCRIPT), "--drill", str(tmp_path), "--runtime-cmd", "false"],
                       capture_output=True, text=True)
    assert r.returncode == 1 and "FAIL #1" in r.stdout and "CLAIMED" not in r.stdout, r.stdout + r.stderr
    assert not (tmp_path / "claims.json").exists()
