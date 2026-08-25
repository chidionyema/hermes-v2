"""crew#182 phone idea flow: the rules a rewrite must keep (spec CP3, CP4, CP5, CP6, CP10, CP11).

Rung 2 (properties) for mode detection, rung 4 (incident, named for the issue) for the gate.
Nothing here talks to GitHub: `create` takes a fake runner, `dedup` takes fixture lists.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("idea_flow", ROOT / "handlers" / "idea_flow.py")
idea_flow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(idea_flow)  # type: ignore[union-attr]


@pytest.fixture(autouse=True)
def _state(tmp_path, monkeypatch):
    monkeypatch.setattr(idea_flow, "STATE", tmp_path / "idea-flow")


EXPLORING = [
    "what if we moved the store to k8s",
    "brainstorm: could the gateway run on OKE?",
    "I'm just exploring, would it make sense to build a second bot",
    "thinking out loud, build nothing yet, what if the board were issues",
]
BUILDING = [
    "build the phone idea flow",
    "write a spec for the dispatcher",
    "implement the icebox label, urgent",
    "remember that idea about the digest, let's do it",
]


@pytest.mark.parametrize("msg", EXPLORING)
def test_cp4_exploratory_phrasing_never_builds(msg):
    assert idea_flow.classify(msg)["mode"] == "explore"


@pytest.mark.parametrize("msg", BUILDING)
def test_cp4_build_phrasing_builds(msg):
    assert idea_flow.classify(msg)["mode"] == "build"


def test_cp10_urgent_is_a_flag_not_a_mode():
    got = idea_flow.classify("urgent: build the pager")
    assert got == {"mode": "build", "urgent": True, "explore_hit": False, "build_hit": True}


def test_cp3_dedup_names_the_existing_card_pr_or_branch():
    got = idea_flow.dedup(
        "phone idea flow dispatcher",
        issues=[{"number": 182, "title": "Phone idea flow: continuity", "labels": [{"name": "agent-go"}]},
                {"number": 9, "title": "Rotate the Svix token", "labels": []}],
        prs=[{"number": 274, "title": "crew-qa: vendor lock-in gate"}],
        branches=["refs/heads/feat/phone-idea-flow"],
    )
    kinds = {(m["kind"], m["id"]) for m in got["matches"]}
    assert len(got["matches"]) == len(kinds), "a match is named once"
    assert ("issue", 182) in kinds and ("branch", "feat/phone-idea-flow") in kinds
    assert ("issue", 9) not in kinds and ("pr", 274) not in kinds


def test_incident_crew182_cp5_no_board_write_without_the_prompt():
    calls = []

    def fake(args):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "https://github.com/chidionyema/crew/issues/999\n", "")

    # No draft shown: refused, nothing written.
    assert "error" in idea_flow.create("deadbeef0000", "todo", run=fake)
    assert calls == []
    # Draft shown, founder chose To Do: one issue, label agent-go.
    d = idea_flow.draft("Pager for the digest", "build a pager for the digest")
    got = idea_flow.create(d["nonce"], "To Do", run=fake)
    assert got["issue"] == 999 and got["labels"] == ["agent-go"]
    assert "--label" in calls[-1] and "agent-go" in calls[-1]
    # The same prompt cannot be answered twice.
    assert "error" in idea_flow.create(d["nonce"], "icebox", run=fake)
    assert len(calls) == 1


def test_cp6_icebox_gets_the_icebox_label_and_drop_writes_nothing():
    calls = []

    def fake(args):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "https://github.com/chidionyema/crew/issues/5\n", "")

    d = idea_flow.draft("Keep this one", "what to keep")
    assert idea_flow.create(d["nonce"], "Icebox", run=fake)["labels"] == ["icebox"]
    d2 = idea_flow.draft("Bin this one", "no")
    assert idea_flow.create(d2["nonce"], "Drop", run=fake) == {"dropped": True, "title": "Bin this one"}
    assert len(calls) == 1


def test_cp10_urgent_draft_carries_the_urgent_label_after_confirmation():
    def fake(args):
        return subprocess.CompletedProcess(args, 0, "https://github.com/chidionyema/crew/issues/7\n", "")

    d = idea_flow.draft("Pager", "drop everything, the pager is down", urgent=True)
    assert idea_flow.create(d["nonce"], "todo", run=fake)["labels"] == ["agent-go", "urgent"]


def test_cp11_the_draft_and_the_choice_are_on_disk_not_only_in_chat():
    d = idea_flow.draft("Persist me", "build persist me")
    rec = json.loads((idea_flow.STATE / f"{d['nonce']}.json").read_text())
    assert rec["feature"].startswith("# features/persist-me.feature") and rec["used"] is False


def test_cp5a_the_prompt_is_three_named_choices_not_a_widget():
    d = idea_flow.draft("Any", "build any")
    assert d["choices"] == ["To Do", "Icebox", "Drop"] and "?" in d["prompt"]
