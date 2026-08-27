"""crew#284 CP2 was dispatched to twice: another session claimed and started real
work (open PR idp#179) at 03:00 UTC; a second session launched a fresh subagent
onto the same checkpoint 15 minutes later, unaware, because the issue body's
checkbox stays ``[ ]`` until the PR merges — an in-progress claim and an
untouched row read identically unless comment history is searched.

scripts/claim-check.py is the fix: a command that must exit 0 before any
subagent is dispatched onto a CPn/checkpoint-labelled issue. This test
reproduces the real incident against the live board (crew#284 CP2, still
open, still claimed) plus the surrounding cases the guard must get right in
both directions: refuse the claimed/closed/blind cases, allow the truly free
one, never crash on malformed input (crew#164: a check that cannot reach its
evidence returns BLIND, never a pass).

Rung 4, incident test, named for the mistake. Network-dependent (calls the
real `gh` CLI against the real chidionyema/crew board) — skipped when `gh` is
absent or unauthenticated rather than failing the suite on an environment gap.
"""

import os
import shutil
import subprocess

import pytest

HOME = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(HOME, "scripts", "claim-check.py")

pytestmark = pytest.mark.skipif(
    shutil.which("gh") is None
    or subprocess.run(["gh", "auth", "status"], capture_output=True).returncode != 0,
    reason="requires an authenticated gh CLI against a live GitHub repo",
)


def _run(*args):
    out = subprocess.run(
        ["python3", SCRIPT, *args], capture_output=True, text=True, timeout=30
    )
    return out.returncode, out.stdout + out.stderr


def test_the_real_incident_is_caught_crew284_cp2_already_claimed():
    """The exact mistake: CP2 on crew#284 was claimed/done first, so dispatch is refused.

    Board state moved since this test was written: CP2 was claimed via a CLAIM comment
    when this test was authored, and is now ticked [x] in the issue body (real progress).
    Both are "already spoken for" -- the guard must refuse either way, so this asserts
    the class (refused, with a reason naming ticked-or-claimed) not one specific wording.
    """
    rc, out = _run("chidionyema/crew", "284", "CP2")
    assert rc == 1
    assert "already ticked" in out or "already claimed" in out


def test_a_checkpoint_with_no_claim_and_no_checkbox_is_allowed():
    """CP7 on crew#284 is open, unticked, and has no CLAIM comment (owned by
    a different issue, crew#227) — the guard must not refuse real free work."""
    rc, out = _run("chidionyema/crew", "284", "CP7")
    assert rc == 0
    assert "Safe to dispatch" in out


def test_a_closed_issue_is_refused():
    rc, out = _run("chidionyema/crew", "35", "CP1")
    assert rc == 1
    assert "CLOSED" in out


def test_a_nonexistent_issue_is_blind_not_a_crash():
    rc, out = _run("chidionyema/crew", "999999999", "CP1")
    assert rc == 2
    assert "BLIND" in out


def test_usage_error_is_a_clean_exit_not_a_traceback():
    rc, out = _run("chidionyema/crew", "284")
    assert rc == 2
    assert "usage:" in out
