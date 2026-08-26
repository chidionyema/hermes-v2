"""crew#66 / R1: "for the last time, we are not going back to fly".

Rung 4, incident: on 2026-08-26 hermes-v2 still carried deploy/fly/ and templates/deploy/fly/
with Fly's API in the egress allow-list while Fly held zero apps. The rule: no Fly deploy
target and no Fly egress in this repo.
"""
from __future__ import annotations

import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[1]
FLY_TARGET = re.compile(r"^(deploy|templates/deploy)/fly/")


def _tracked() -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return out.splitlines()


def test_incident_crew66_no_fly_deploy_target():
    assert not [f for f in _tracked() if FLY_TARGET.match(f)]


def test_incident_crew66_no_fly_egress():
    hits = [p for p in ROOT.glob("templates/**/egress-allowlist.txt.tmpl")
            if "api.fly.io" in p.read_text(encoding="utf-8").split()]
    assert not hits, hits


def test_the_guard_refuses_a_fly_target():
    assert FLY_TARGET.match("deploy/fly/fly.toml") and FLY_TARGET.match("templates/deploy/fly/x.tmpl")
    assert not FLY_TARGET.match("deploy/k8s/gateway.yaml")
