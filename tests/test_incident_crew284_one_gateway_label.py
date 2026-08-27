"""crew#284, 2026-08-27 00:4xZ: two launchd labels ran the same gateway and killed each other.

Rung 4 (incident). `ai.hermes.gateway` was retired but its plist stayed in ~/Library/LaunchAgents;
a session loaded it, and it fought `ai.architect.gateway` (both KeepAlive, both `--replace`) with a
new gateway pid every ~10s. The `bin/verify` row "one launchd label runs the gateway" must fail on a
second loaded gateway label and on a retired plist left on disk, and pass on exactly one label.
Proved both ways here with a `launchctl` shim on PATH and a scratch HOME.
"""
import os
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROW = "one launchd label runs the gateway"


def _row(tmp_path, labels, retired_plist=False):
    shim = tmp_path / "bin"
    shim.mkdir()
    lines = "".join(f'printf "%s\\t0\\t%s\\n" 1{i} {l}; ' for i, l in enumerate(labels))
    (shim / "launchctl").write_text(
        "#!/bin/sh\n" f'if [ "$1" = list ]; then {lines} exit 0; fi\n' 'exec /bin/launchctl "$@"\n'
    )
    (shim / "launchctl").chmod(0o755)
    home = tmp_path / "home"
    (home / "Library" / "LaunchAgents").mkdir(parents=True)
    if retired_plist:
        (home / "Library" / "LaunchAgents" / "ai.hermes.gateway.plist").write_text("<plist/>")
    env = {**os.environ, "PATH": f"{shim}:{os.environ['PATH']}", "HOME": str(home)}
    p = subprocess.run(["bash", os.path.join(ROOT, "bin", "verify")], capture_output=True, text=True, env=env, cwd=ROOT, timeout=300)
    hits = [l for l in p.stdout.splitlines() if ROW in l]
    assert len(hits) == 1, p.stdout + p.stderr
    return hits[0]


def test_one_label_passes(tmp_path):
    assert "PASS" in _row(tmp_path, ["ai.architect.gateway"])


def test_a_second_gateway_label_fails(tmp_path):
    line = _row(tmp_path, ["ai.architect.gateway", "ai.hermes.gateway"])
    assert "FAIL" in line and "ai.hermes.gateway" in line


def test_a_retired_plist_on_disk_fails(tmp_path):
    line = _row(tmp_path, ["ai.architect.gateway"], retired_plist=True)
    assert "FAIL" in line and "retired plist on disk: ai.hermes.gateway" in line
