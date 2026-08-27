"""crew#506 CP3 (2026-08-27): the compaction trigger must leave room for the fallback hop.

Measured (hermes-v2/state.db, session_model_usage, 2026-08-27): fixed per-call overhead on a
fresh Telegram session is 43-45k input tokens; the long session ran 373k-412k per call. The one
fallback hop is the router's minimax lane at 204,800 input tokens. Rule: threshold_tokens plus
the measured overhead fits the smallest window in the fallback path. Both ways: main's 200,000
trigger fails (200k + 45k > 204.8k)."""
from __future__ import annotations

import os
import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
CFG = ROOT / "config.yaml"
MEASURED_OVERHEAD_TOKENS = 45_000  # fresh-session input per call, 2026-08-27 (see module docstring)
FALLBACK_WINDOW_TOKENS = 204_800   # idp/platform/llm/config.yaml, minimax max_input_tokens


def _fallback_window() -> int:
    """Prefer the router config when the estate checkout is present; the constant otherwise."""
    router = pathlib.Path(os.environ.get("ESTATE_CODE", ROOT.parent)) / "idp" / "platform" / "llm" / "config.yaml"
    if router.is_file():
        for entry in yaml.safe_load(router.read_text()).get("model_list", []):
            if entry.get("model_name") == "minimax":
                found = (entry.get("model_info") or {}).get("max_input_tokens") or (entry.get("litellm_params") or {}).get("max_input_tokens")
                if found:
                    return int(found)
    return FALLBACK_WINDOW_TOKENS


def test_compaction_trigger_plus_overhead_fits_the_fallback_hop() -> None:
    cfg = yaml.safe_load(CFG.read_text())
    trigger = int(cfg["compression"]["threshold_tokens"])
    window = _fallback_window()
    assert trigger + MEASURED_OVERHEAD_TOKENS <= window, (
        f"threshold_tokens {trigger} + overhead {MEASURED_OVERHEAD_TOKENS} = {trigger + MEASURED_OVERHEAD_TOKENS} "
        f"exceeds the fallback window {window}; the hop can never take a turn that compaction has not yet cut")


def test_the_guard_refuses_the_measured_config() -> None:
    assert 200_000 + MEASURED_OVERHEAD_TOKENS > FALLBACK_WINDOW_TOKENS, "main's 200k trigger must fail this rule"
