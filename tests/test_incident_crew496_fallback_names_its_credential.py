"""Incident test, crew#496 (2026-08-27): Otto refused six founder turns and the configured fallback
(openai gpt-5.4) could not take over because nothing named its credential; the refusal reached the
founder as silence. Rule: every fallback_providers entry in config.yaml is a custom endpoint that
names its base_url and the env var holding its key, so a fallback is never a wish (LAW 44).
Rung 4, both ways: the committed config passes; an entry without key_env is named.
"""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config.yaml"


def _unfunded(entries: list) -> list[str]:
    bad = []
    for i, e in enumerate(entries or [], 1):
        if not (e.get("base_url") and e.get("key_env")):
            bad.append(f"fallback {i}: provider={e.get('provider')} model={e.get('model')} lacks base_url+key_env")
    return bad


def test_incident_crew496_committed_fallbacks_name_a_credential():
    cfg = yaml.safe_load(CONFIG.read_text())
    assert cfg.get("fallback_providers"), "a gateway with no fallback fails silent on the first refusal"
    assert _unfunded(cfg["fallback_providers"]) == []


def test_incident_crew496_a_fallback_without_key_env_is_named():
    assert _unfunded([{"provider": "openai", "model": "gpt-5.4"}]) == [
        "fallback 1: provider=openai model=gpt-5.4 lacks base_url+key_env"
    ]
