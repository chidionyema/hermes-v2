"""No file the running agent writes may be tracked in git.

The repo and the agent's home are the same directory, so every tick of the
gateway writes into the working tree. When one of those files is tracked, the
repo is permanently dirty and `git add -A` sweeps live state into a commit.

That happened: b0d65e0 committed channel_directory.json, cron/ticker_heartbeat,
cron/ticker_last_success and state/gateway.heartbeat. Every gateway tick then
showed as a diff.

Rung 3 in the ladder: the mistake is now refused by a machine, not remembered.
"""
import os
import re
import subprocess

HOME = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Names and shapes that only ever mean "a process wrote this while running".
RUNTIME = [
    re.compile(r"(^|/)state/"),
    re.compile(r"heartbeat"),
    re.compile(r"\.pid$"),
    re.compile(r"\.lock$"),
    re.compile(r"\.db(-wal|-shm)?$"),
    re.compile(r"(^|/)channel_directory\.json$"),
    re.compile(r"(^|/)gateway[_-].*\.(json|log)$"),
    re.compile(r"(^|/)sessions/"),
    re.compile(r"(^|/)memories/"),
    re.compile(r"(^|/)logs/"),
    re.compile(r"(^|/)cache/"),
    re.compile(r"ticker_"),
]


def tracked():
    out = subprocess.run(
        ["git", "-C", HOME, "ls-files"], capture_output=True, text=True, check=True
    ).stdout
    return [p for p in out.split("\n") if p]


def test_no_runtime_file_is_tracked():
    bad = [p for p in tracked() if any(r.search(p) for r in RUNTIME)]
    assert not bad, (
        "these are written by the running agent and must not be tracked:\n  "
        + "\n  ".join(bad)
        + "\nUntrack with: git rm --cached <path>, then add it to .gitignore."
    )


def test_gitignore_covers_what_the_gateway_writes():
    # Ignoring them is what stops `git add -A` putting them back.
    with open(os.path.join(HOME, ".gitignore")) as f:
        ignored = f.read()
    for name in (
        "channel_directory.json",
        "cron/ticker_heartbeat",
        "cron/ticker_last_success",
        "state/",
    ):
        assert name in ignored, f".gitignore does not cover {name}"
