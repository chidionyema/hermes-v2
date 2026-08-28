"""crew#284, 2026-08-27 00:4xZ: two launchd labels ran the same gateway and killed each other.

Rung 4 (incident). `ai.hermes.gateway` was retired but its plist stayed in ~/Library/LaunchAgents;
a session loaded it, and it fought `ai.architect.gateway` (both KeepAlive, both `--replace`) with a
new gateway pid every ~10s.

crew#516, 2026-08-28: the gateway moved to the cluster and BOTH labels are retired, so the row this
file guards was renamed and inverted -- a loaded gateway label on this Mac is now always wrong, not
only the second one. The crew#284 guarantee survives unchanged inside that: a retired plist left on
disk fails on the file alone, before anything loads it. The "exactly one is correct" case moved to
test_incident_crew516_the_mac_does_not_run_the_gateway.py, which owns the inversion.
Proved with a `launchctl` shim on PATH and a scratch HOME.
"""
import os
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROW = "the Mac does not run the gateway"


def _row(tmp_path, labels, plists=()):
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
    p = subprocess.run(["bash", os.path.join(ROOT, "bin", "verify")], capture_output=True, text=True, env=env, cwd=ROOT, timeout=300)
    hits = [l for l in p.stdout.splitlines() if ROW in l]
    assert len(hits) == 1, p.stdout + p.stderr
    return hits[0]


def test_two_gateway_labels_fail_and_both_are_named(tmp_path):
    line = _row(tmp_path, ["ai.architect.gateway", "ai.hermes.gateway"])
    assert "FAIL" in line and "ai.hermes.gateway" in line and "ai.architect.gateway" in line


def test_a_retired_plist_on_disk_fails_before_anything_loads_it(tmp_path):
    """The crew#284 loaded gun: nothing is running it, and the row still fails on the file."""
    line = _row(tmp_path, [], plists=["ai.hermes.gateway"])
    assert "FAIL" in line and "retired plist on disk: ai.hermes.gateway" in line


def test_the_architect_plist_is_a_loaded_gun_too(tmp_path):
    """crew#516: what was the live definition on 2026-08-27 is now the same gun as its predecessor."""
    line = _row(tmp_path, [], plists=["ai.architect.gateway"])
    assert "FAIL" in line and "ai.architect.gateway" in line
