"""crew#516: the verifier demanded the trap it was meant to prevent, so sessions kept rebuilding it.

The gateway moved to the cluster (platform/hermes-agent) and one Telegram token admits exactly one
poller: whoever is polling holds it, and the other side gets 409 "terminated by other getUpdates
request" forever. On 2026-08-27/28 The Architect answered nobody on Telegram for 8h45m because a
Mac poller held the token while the cluster pod, the one with the fixed image, could not.

The Mac poller kept coming back, and telling sessions not to revive it did not stop it -- a peer
rewrote the plist at 08:43 on 2026-08-28, minutes after it was parked. It came back because
`bin/verify` row 12 FAILED when the plist was absent ("no plist at ...") and row 12b FAILED when no
gateway label was loaded ("no gateway label is loaded"). Every session that ran the verifier was
told this machine was broken, and repaired it by putting the second poller back. The trap was the
instrument, not the session.

Rule: on this Mac, zero gateway labels loaded and zero gateway plists on disk is the PASS. Rung 4,
incident test: the state that used to fail is the only state that passes, and the reviving
instruction is gone from the docs that told sessions to run it.
"""
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROW = "the Mac does not run the gateway"


def _rows(tmp_path, labels=(), plists=()):
    shim = tmp_path / "bin"
    shim.mkdir()
    lines = "".join(f'printf "%s\\t0\\t%s\\n" 1{i} {l}; ' for i, l in enumerate(labels))
    (shim / "launchctl").write_text(
        "#!/bin/sh\n" f'if [ "$1" = list ]; then {lines} exit 0; fi\n' 'exec /bin/launchctl "$@"\n'
    )
    (shim / "launchctl").chmod(0o755)
    home = tmp_path / "home"
    (home / "Library" / "LaunchAgents").mkdir(parents=True)
    for name in plists:
        (home / "Library" / "LaunchAgents" / f"{name}.plist").write_text("<plist/>")
    env = {**os.environ, "PATH": f"{shim}:{os.environ['PATH']}", "HOME": str(home)}
    p = subprocess.run(["bash", str(ROOT / "bin" / "verify")], capture_output=True, text=True,
                       env=env, cwd=ROOT, timeout=300)
    return p.stdout + p.stderr


def _one(out, row):
    hits = [l for l in out.splitlines() if row in l]
    assert len(hits) == 1, out
    return hits[0]


def test_a_mac_with_no_gateway_at_all_passes(tmp_path):
    """The exact state the cutover left, and the exact state the old row called broken."""
    line = _one(_rows(tmp_path), ROW)
    assert "PASS" in line, line


def test_the_verifier_never_asks_for_a_gateway_plist_on_this_mac(tmp_path):
    """The revival was a repair. No row may report the absence of a gateway plist as a defect."""
    out = _rows(tmp_path)
    for line in out.splitlines():
        if "FAIL" in line:
            assert "no plist at" not in line and "is not loaded" not in line, line
            assert "no gateway label is loaded" not in line, line


def test_a_single_loaded_gateway_label_is_now_a_failure(tmp_path):
    """One was correct until the cutover. One is now the second poller."""
    line = _one(_rows(tmp_path, labels=["ai.architect.gateway"]), ROW)
    assert "FAIL" in line and "ai.architect.gateway" in line


def test_drift_is_still_checked_and_no_longer_only_for_the_gateway(tmp_path):
    """Row 12 compared one label -- the retired one. Losing it would lose the drift check for the
    seven jobs that do still run here, so it reads every loaded label with a plist instead."""
    line = _one(_rows(tmp_path), "launchd runs the plists on disk")
    assert "PASS" in line, line
    text = (ROOT / "bin" / "verify").read_text()
    assert 'for PLIST in "$HOME"/Library/LaunchAgents/*.plist' in text
    assert 'LABEL="ai.architect.gateway"' not in text


def test_no_document_hands_a_session_the_command_that_revives_it():
    """A doc that prints `launchctl bootstrap ... ai.architect.gateway.plist` is the trap in prose:
    the next session reads it, runs it, and the founder's agent goes mute again."""
    # The command, not the word: prose that merely says a label was loaded is a description, and
    # `launchctl bootstrap`/`load`/`start` on a gateway label is an instruction someone will follow.
    starts = re.compile(r"launchctl\s+(bootstrap|load|start)\b")
    offenders = []
    for path in list(ROOT.glob("docs/**/*.md")) + [ROOT / "SOUL.md", ROOT / "README.md"]:
        if not path.is_file():
            continue
        for line in path.read_text(errors="ignore").splitlines():
            if starts.search(line) and "gateway" in line:
                offenders.append(f"{path.relative_to(ROOT)}: {line.strip()}")
    assert not offenders, "\n".join(offenders)


# Every file a session actually walks: the docs, plus the two executables that print
# instructions to a human. `bin/verify` is in here because row 10 printed
# `start it: ./bin/hermes gateway install` on an IDLE row -- the verifier itself was
# handing out the revival command while rows 12/12b were refusing it.
WALKED = ("docs/**/*.md",)
WALKED_FILES = ("SOUL.md", "README.md", "install", "bin/teardown", "bin/verify")

# `hermes` as the program token, `gateway <verb>` as its arguments -- the same shape
# rule second_telegram_poller refuses in claude-guards, so the two halves agree.
CLI_START = re.compile(
    r"(?:^|[\s;&|(`])(?:[\w./-]*/)?hermes(?:_cli(?:\.main)?)?"
    r"(?:\s+-{1,2}[\w-]+(?:=\S+)?)*\s+gateway\s+(?:run|install|start|restart)\b"
)
ONE_TOKEN = ("one poller", "one telegram token", "one token")


def _walked_paths():
    out = []
    for pat in WALKED:
        out += sorted(ROOT.glob(pat))
    out += [ROOT / f for f in WALKED_FILES]
    return [p for p in out if p.is_file()]


def test_a_start_command_never_stands_without_the_one_token_rule_beside_it():
    """The first version of this incident test only knew `launchctl bootstrap`. It passed while
    `bin/verify` row 10, `bin/teardown`, the installer and the README were all still printing
    `./bin/hermes gateway install` -- the shape the next session was actually going to copy.

    Deleting the command outright is wrong: on a NEW estate with a bot token of its own it is the
    correct instruction. What must never happen is that it stands alone. Every place that prints
    it has to say, within sight, that one token admits one poller."""
    offenders = []
    for path in _walked_paths():
        lines = path.read_text(errors="ignore").splitlines()
        for i, line in enumerate(lines):
            if line.lstrip().startswith("#") or not CLI_START.search(line):
                continue
            # Normalised: the warning is prose and wraps, so "exactly one\n  poller" has to
            # count, and so does a capitalised "One token" at the start of a sentence.
            window = " ".join(" ".join(lines[max(0, i - 8): i + 9]).split()).lower()
            if not any(k in window for k in ONE_TOKEN):
                offenders.append(f"{path.relative_to(ROOT)}:{i + 1}: {line.strip()}")
    assert not offenders, (
        "a gateway-start command with no one-token warning within 8 lines:\n"
        + "\n".join(offenders)
    )
