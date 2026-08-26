"""crew#278 CP3: the fallback provider must differ from the primary, or an outage at one vendor is an outage of Otto.

Rung 4, incident: config.yaml had fallback_providers = anthropic/claude-sonnet-5, the same as
model.default, measured 2026-08-26. Founder: "I need to be able to trust Otto's judgement."
"""
from __future__ import annotations

import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _cfg():
    return yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))


def test_incident_crew278_fallback_is_a_different_provider():
    cfg = _cfg()
    primary = cfg["model"]["provider"]
    fallbacks = cfg.get("fallback_providers") or []
    assert fallbacks, "no fallback provider at all"
    assert any(f.get("provider") != primary for f in fallbacks), (
        f"every fallback is on {primary!r}, the same vendor as the primary")


def test_the_guard_refuses_the_measured_config():
    bad = {"model": {"provider": "anthropic"}, "fallback_providers": [{"provider": "anthropic", "model": "x"}]}
    assert not any(f["provider"] != bad["model"]["provider"] for f in bad["fallback_providers"])
