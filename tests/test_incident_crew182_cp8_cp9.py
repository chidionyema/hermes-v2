"""crew#182 CP8 (welcome back) and CP9 (later activation). Rung 4, one incident test per rule.

CP8 guards the first live run, where the dispatched branch was cut from whatever the
laptop had checked out (feat/research-engine-step1) instead of the remote default branch:
branch A is untouched, branch B is based on origin/main, and a merge check reports no conflict.
CP9 guards the gate: activating an Icebox issue drafts again and never moves the card by itself.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "dispatch-agent-go.py"
spec = importlib.util.spec_from_file_location("idea_flow", ROOT / "handlers" / "idea_flow.py")
idea_flow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(idea_flow)  # type: ignore[union-attr]


def _git(cwd, *a):
    return subprocess.run(["git", "-C", str(cwd), "-c", "user.email=t@t", "-c", "user.name=t", *a],
                          check=True, capture_output=True, text=True).stdout.strip()


def test_incident_crew182_cp8_phone_branch_is_cut_from_origin_main_and_laptop_branch_is_untouched(tmp_path):
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    co = tmp_path / "checkout"
    _git(tmp_path, "clone", "-q", str(origin), str(co))
    (co / "shared.txt").write_text("base\n")
    _git(co, "add", "shared.txt")
    _git(co, "commit", "-q", "-m", "base")
    _git(co, "push", "-q", "origin", "main")
    # Branch A: the laptop session, one commit ahead and one file uncommitted.
    _git(co, "checkout", "-q", "-b", "feat/laptop")
    (co / "laptop.txt").write_text("laptop work\n")
    _git(co, "add", "laptop.txt")
    _git(co, "commit", "-q", "-m", "laptop")
    (co / "dirty.txt").write_text("not committed\n")
    a_head = _git(co, "rev-parse", "HEAD")

    (tmp_path / "issues.json").write_text(json.dumps([
        {"number": 9, "title": "phone idea", "body": "Repo: drill/checkout", "labels": [{"name": "agent-go"}]},
    ]))
    r = subprocess.run([sys.executable, str(SCRIPT), "--drill", str(tmp_path), "--runtime-cmd",
                        "sh -c 'echo phone > phone.txt && git add phone.txt && git -c user.email=t@t -c user.name=t commit -q -m phone'"],
                       capture_output=True, text=True)
    assert r.returncode == 0 and "CLAIMED #9" in r.stdout, r.stdout + r.stderr

    # A is exactly where the session left it.
    assert _git(co, "rev-parse", "HEAD") == a_head
    assert _git(co, "rev-parse", "--abbrev-ref", "HEAD") == "feat/laptop"
    assert (co / "dirty.txt").read_text() == "not committed\n"
    # B is based on origin/main, not on A.
    assert _git(co, "merge-base", "agent-go/9", "origin/main") == _git(co, "rev-parse", "origin/main")
    assert subprocess.run(["git", "-C", str(co), "merge-base", "--is-ancestor", "feat/laptop", "agent-go/9"]).returncode == 1
    # A merge check between A and B reports no conflict.
    mt = subprocess.run(["git", "-C", str(co), "merge-tree", "--write-tree", "feat/laptop", "agent-go/9"],
                        capture_output=True, text=True)
    assert mt.returncode == 0, mt.stdout + mt.stderr


def test_incident_crew182_cp9_activating_an_icebox_issue_drafts_again_and_never_moves_the_card(tmp_path, monkeypatch):
    monkeypatch.setattr(idea_flow, "STATE", tmp_path / "idea-flow")
    gh_calls = []

    def fake_gh_json(args):
        gh_calls.append(args)
        return [{"number": 41, "title": "Move the store to OKE", "body": "# RFC\nkept idea"}]

    monkeypatch.setattr(idea_flow, "_gh_json", fake_gh_json)
    monkeypatch.setattr(idea_flow.subprocess, "run",
                        lambda *a, **k: pytest.fail("activate must not run gh outside the read: %r" % (a,)))

    d = idea_flow.activate("remember that idea, let's do it: move the store to OKE")
    assert d["icebox_issue"] == 41 and "Feature:" in d["feature"] and d["nonce"]
    assert gh_calls == [["issue", "list", "-R", idea_flow.REPO, "--label", "icebox", "--state", "open",
                         "--limit", "100", "--json", "number,title,body"]]
    # Only the same confirmation gate moves it: create() with the nonce and the founder's tap.
    moved = []

    def fake_run(args):
        moved.append(args)
        return subprocess.CompletedProcess(args, 0, "https://github.com/chidionyema/crew/issues/42\n", "")

    assert idea_flow.create("", "todo", run=fake_run)["error"].startswith("no draft")
    assert moved == []
    out = idea_flow.create(d["nonce"], "To Do", run=fake_run)
    assert out["labels"] == ["agent-go"] and len(moved) == 1
    # Unknown reference: nothing drafted, the icebox list is shown instead.
    assert "error" in idea_flow.activate("#999")
