"""Incident 2026-08-26 (rung 4): the live estate.yaml still named Fly two days
after R1. bin/verify must refuse it and must pass a clean file."""
import subprocess
import sys
from pathlib import Path

CHECK = Path(__file__).resolve().parents[1] / "bin" / "check-platform.py"


def run(tmp_path, text):
    f = tmp_path / "estate.yaml"
    f.write_text(text)
    return subprocess.run([sys.executable, str(CHECK), str(f)], capture_output=True, text=True)


def test_fly_url_or_kind_is_refused(tmp_path):
    r = run(tmp_path, "services:\n  - url: https://x.fly.dev/\nplatform:\n  kind: fly\n  cli: flyctl\n")
    assert r.returncode == 1 and r.stdout.count("R1") == 3, r.stdout


def test_kubernetes_estate_passes(tmp_path):
    r = run(tmp_path, "services:\n  - url: https://acme.example.com/\nplatform:\n  kind: kubernetes\n  cli: kubectl\n")
    assert r.returncode == 0 and "no dead platform" in r.stdout, r.stdout
