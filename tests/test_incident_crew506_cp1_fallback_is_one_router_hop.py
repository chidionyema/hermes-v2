"""crew#506 CP1 (2026-08-27): Otto's config.yaml listed two router aliases as fallbacks
(minimax, then deepseek) while the estate router already chained minimax -> deepseek itself
(idp#409). Two owners of one decision: on a bad minute Hermes re-walked the router's chain a
second time, doubling retries and latency. Rule: exactly one fallback entry, and it is the
estate router (the chain behind it is the router's, in idp/platform/llm/config.yaml).
Both ways: main's two-entry config fails this."""
from __future__ import annotations

import pathlib

import yaml

CFG = pathlib.Path(__file__).resolve().parents[1] / "config.yaml"


def test_fallback_is_exactly_one_router_hop() -> None:
    cfg = yaml.safe_load(CFG.read_text())
    fallbacks = cfg["fallback_providers"]
    assert len(fallbacks) == 1, f"the router owns the chain; Hermes lists one hop, found {[f['model'] for f in fallbacks]}"
    hop = fallbacks[0]
    assert hop["provider"] == "custom" and hop["base_url"].rstrip("/").endswith("/v1")
    assert "llm." in hop["base_url"], "the hop must be the estate router, not a vendor"
    assert hop["key_env"] == "LITELLM_API_KEY"


def test_the_guard_refuses_the_measured_config() -> None:
    bad = {"fallback_providers": [
        {"provider": "custom", "model": "minimax", "base_url": "https://llm.example/v1", "key_env": "LITELLM_API_KEY"},
        {"provider": "custom", "model": "deepseek", "base_url": "https://llm.example/v1", "key_env": "LITELLM_API_KEY"},
    ]}
    assert len(bad["fallback_providers"]) != 1
