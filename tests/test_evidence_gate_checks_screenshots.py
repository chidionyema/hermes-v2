"""The §7 evidence gate has to check the picture, not only the pasted text.

Found 2026-08-22: `ci/evidence-gate.js` read the pull request body for fenced
command output and nothing else. Pasted text is typed by hand in seconds and
reads identically whether the command ran or not, so the gate passed a claim
with no run behind it. That is the exact failure the gate exists to stop.

LAW 22 closes it: the pull request carries a screenshot of the thing passing,
committed into its own branch under docs/evidence/pr-<n>/ so it travels in the
git bundle rather than living in GitHub's attachment store.

Rung 4, one incident test named for the bug. It asserts the rule - the gate
checks images, and it checks them where LAW 22 says they live - not the shape
of the shell that does it.
"""
import os

import yaml

HOME = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATE = os.path.join(HOME, "ci", "evidence-gate.yml")


def steps():
    with open(GATE) as f:
        return yaml.safe_load(f)["jobs"]["gate"]["steps"]


def gate_text():
    with open(GATE) as f:
        return f.read()


def test_the_gate_runs_the_text_check():
    body = " ".join(s.get("run", "") for s in steps())
    assert "ci/evidence-gate.js" in body, "the pasted-text check is gone from the gate"


def test_the_gate_also_checks_for_a_screenshot():
    body = " ".join(s.get("run", "") for s in steps())
    assert ".png" in body, (
        "the gate checks pasted text only. A hand-typed claim passes it.\n"
        "LAW 22: the pull request must carry a picture of the run."
    )


def test_the_screenshot_is_looked_for_where_law_22_puts_it():
    # Not GitHub's attachment store. In the branch, so it leaves with the code.
    body = " ".join(s.get("run", "") for s in steps())
    assert "docs/evidence/pr-" in body, (
        "the gate does not look in docs/evidence/pr-<n>/, so a screenshot that "
        "satisfies it would not travel in the git bundle"
    )


def test_a_missing_screenshot_fails_rather_than_warns():
    # A gate that prints a warning and exits 0 is a gate that does nothing.
    shot_step = [s for s in steps() if ".png" in s.get("run", "")]
    assert shot_step, "no screenshot step at all"
    assert "exit 1" in shot_step[0]["run"], (
        "the screenshot step never exits non-zero, so it cannot block a pull request"
    )
