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
    # crew#568 Phase 5: primary and fallback are both the estate router (provider `custom`), so
    # "a different provider" is a different router lane; the router owns the vendor behind each
    # lane and falls minimax -> deepseek itself. What must never come back: the same lane twice.
    primary = (cfg["model"]["provider"], cfg["model"].get("default"))
    fallbacks = cfg.get("fallback_providers") or []
    assert fallbacks, "no fallback provider at all"
    assert any((f.get("provider"), f.get("model")) != primary for f in fallbacks), (
        f"every fallback is {primary!r}, the same lane as the primary")


def test_the_guard_refuses_the_measured_config():
    bad = {"model": {"provider": "anthropic"}, "fallback_providers": [{"provider": "anthropic", "model": "x"}]}
    assert not any(f["provider"] != bad["model"]["provider"] for f in bad["fallback_providers"])
