"""The on/off switch has to be real, and it has to edit only its own block.

Two incidents, both on 2026-08-22, both while building this file:

  * `bin/features work on` rewrote `models.work` - a model name - to the string
    `on`, because the search for the line matched the first `work:` in the file
    and `models:` has one too. The edit is now confined to the features block.

  * A lane being "off" has to mean no job exists. If the switch only changed a
    label and the cron jobs were created anyway, the lane would still fire and
    still cost money.

Rung 3: both are refused by a machine now.
"""
import os
import subprocess
import sys

HOME = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FEATURES = os.path.join(HOME, "bin", "features")
ESTATE = os.path.join(HOME, "estate.yaml")
INSTALL_CRON = os.path.join(HOME, "bin", "install-cron.py")

LANES = ["watch", "sunday_rituals", "work", "evolution", "bench", "screenshot_handler"]


def run(*args):
    return subprocess.run([sys.executable, *args], capture_output=True, text=True, cwd=HOME)


def estate_text():
    with open(ESTATE) as f:
        return f.read()


def block(name):
    """The lines of one top-level yaml block, so a test can look at it alone."""
    lines = estate_text().split("\n")
    start = next(i for i, l in enumerate(lines) if l.rstrip() == f"{name}:")
    out = []
    for line in lines[start + 1:]:
        if line.strip() and not line.startswith((" ", "\t")):
            break
        out.append(line)
    return "\n".join(out)


def test_every_lane_is_listed_and_readable():
    r = run(FEATURES)
    assert r.returncode == 0, r.stderr
    for lane in LANES:
        assert lane in r.stdout, f"bin/features does not mention {lane}"


def test_check_exits_zero_for_on_and_one_for_off():
    # watch ships on, work ships off. The exit code is what install-cron reads.
    assert run(FEATURES, "--check", "watch").returncode == 0
    assert run(FEATURES, "--check", "work").returncode == 1


def test_flipping_a_lane_does_not_touch_any_other_block():
    before_models = block("models")
    before_limits = block("limits")
    try:
        assert run(FEATURES, "work", "on").returncode == 0
        assert run(FEATURES, "--check", "work").returncode == 0, "flip did not persist"
        assert block("models") == before_models, (
            "flipping a feature rewrote the models block:\n" + block("models")
        )
        assert block("limits") == before_limits, "flipping a feature rewrote limits"
    finally:
        run(FEATURES, "work", "off")
    assert run(FEATURES, "--check", "work").returncode == 1


def test_an_off_lane_creates_no_jobs():
    assert run(FEATURES, "--check", "work").returncode == 1, "work should ship off"
    r = run(INSTALL_CRON, "cron/work.jobs", "--feature", "work")
    assert r.returncode == 0
    assert "no jobs were created" in r.stdout, r.stdout
    assert "created work-" not in r.stdout, "an off lane created jobs:\n" + r.stdout


def test_an_unknown_lane_is_refused_rather_than_ignored():
    # A typo in estate.yaml that silently means "off" is how a lane goes quiet.
    r = run(FEATURES, "--check", "definitely_not_a_lane")
    assert r.returncode != 0
    assert "definitely_not_a_lane" in (r.stdout + r.stderr)
