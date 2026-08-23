"""A reply said DONE with edited files and no verification run behind it.

Measured 2026-08-24 on this estate: the live verification ledger
(verification_evidence.db) held 0 events while the Architect answered the
founder's only surface, Telegram — so nothing joined a completion claim to a
green run, and a false "done" cost nothing to say. Research for crew #63 lane
A: a ledger join detects unproven claims at 91% against 35% for prose
word-lists, so the trigger is the mandated ``DONE:`` status line, never
word-matching.

The rule (crew #63 A2): a ``DONE:`` status line is rewritten ``UNVERIFIED:``
with one footer line when the session's ledger shows verifiable edits with no
green run after the last edit. Stamp only — the gate never blocks, empties,
or bounces a reply, and it fails open (LAW 38: a guard that refuses correct
work is an outage). Paired controls both directions: the stamp fires on the
unproven claim AND stays away from the proven one, the doc-only one, the
WORKING/BLOCKED one, and the unknown session.

Rung 4, incident tests, named for the behaviour.
"""

import os
import subprocess
import sys

import pytest

HOME = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENT_ROOT = os.path.join(HOME, "hermes-agent")
sys.path.insert(0, AGENT_ROOT)


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    """A hermetic hermes home plus a git-rooted project the ledger accepts."""
    home = tmp_path / "hermes-home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("HERMES_CLAIM_GATE_DISABLED", raising=False)
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(
        ["git", "init", "-q", str(project)], check=True, capture_output=True
    )
    return project


def _edit(project, session, paths):
    from agent.verification_evidence import mark_workspace_edited

    state = mark_workspace_edited(
        session_id=session, cwd=str(project), paths=paths
    )
    assert state is not None, "test project must resolve as a workspace"


def _green_run(project, session):
    from agent.verification_evidence import record_verify_run

    assert record_verify_run(root=str(project), session_id=session, ok=True)


def _stamp(text, session):
    from gateway.claim_gate import stamp_unproven_done

    return stamp_unproven_done(text, session_id=session)


def test_a_done_claim_with_no_green_run_is_stamped(workspace):
    _edit(workspace, "s-1", ["maestro.py"])
    out = _stamp("DONE: shipped the fix.", "s-1")
    assert out.startswith("UNVERIFIED: shipped the fix.")
    assert "UNVERIFIED: files were edited" in out
    assert "project: unverified" in out, "the footer names the root and why"


def test_a_done_claim_with_a_fresh_green_run_passes_untouched(workspace):
    _edit(workspace, "s-2", ["maestro.py"])
    _green_run(workspace, "s-2")
    text = "DONE: shipped the fix, suite green."
    assert _stamp(text, "s-2") == text, \
        "a gate that stamps proven work is an outage, not a guard"


def test_a_green_run_older_than_the_last_edit_is_stale_and_stamps(workspace):
    _edit(workspace, "s-3", ["maestro.py"])
    _green_run(workspace, "s-3")
    _edit(workspace, "s-3", ["maestro.py"])
    out = _stamp("DONE: fixed.", "s-3")
    assert out.startswith("UNVERIFIED:")
    assert "stale" in out


def test_working_and_blocked_are_never_stamped(workspace):
    _edit(workspace, "s-4", ["maestro.py"])
    for text in ("WORKING: half way through.", "BLOCKED: waiting on CI."):
        assert _stamp(text, "s-4") == text, \
            "only a completion claim needs proof; progress reports claim nothing"


def test_the_label_prefix_does_not_hide_the_claim(workspace):
    _edit(workspace, "s-5", ["maestro.py"])
    out = _stamp("[Architect] DONE: shipped.", "s-5")
    assert out.startswith("[Architect] UNVERIFIED: shipped.")


def test_doc_only_edits_pass_untouched(workspace):
    _edit(workspace, "s-6", ["README.md", "docs/onboarding/gate.md"])
    text = "DONE: rewrote the onboarding page."
    assert _stamp(text, "s-6") == text, \
        "prose has nothing a test run could prove; stamping it teaches distrust"


def test_a_session_with_no_ledger_rows_passes(workspace):
    text = "DONE: answered from memory, no edits."
    assert _stamp(text, "s-never-seen") == text


def test_the_gate_fails_open_when_the_ledger_breaks(workspace, monkeypatch):
    _edit(workspace, "s-7", ["maestro.py"])
    import agent.verification_evidence as ve

    def _boom(session_id):
        raise RuntimeError("ledger unavailable")

    monkeypatch.setattr(ve, "session_verification_gaps", _boom)
    text = "DONE: shipped."
    assert _stamp(text, "s-7") == text, \
        "a broken gate must degrade to no gate, never to a broken reply"


def test_the_escape_hatch_disables_the_stamp(workspace, monkeypatch):
    _edit(workspace, "s-8", ["maestro.py"])
    monkeypatch.setenv("HERMES_CLAIM_GATE_DISABLED", "1")
    text = "DONE: shipped."
    assert _stamp(text, "s-8") == text


def test_the_gate_is_wired_into_the_reply_path():
    """Single textual angle; the live second angle is the gateway log line."""
    run_py = os.path.join(AGENT_ROOT, "gateway", "run.py")
    with open(run_py) as f:
        src = f.read()
    assert "stamp_unproven_done" in src, \
        "a gate no reply passes through is an instrument nobody reads"
    sanitize = src.index("_sanitize_gateway_final_response(source.platform, response)")
    assert "stamp_unproven_done" in src[sanitize:sanitize + 800], \
        "the stamp must run at the same choke point as the sanitizer"
