"""crew#751: the Architect (Otto's Telegram brain) stays on the estate router.

ACP as the Telegram brain was the invasive path (fork patches, unknown-method hang, no stream).
Cursor is the WORK runtime, reached from the Mac through mac-run (idp platform/hermes-agent,
graded by idp's test_incident_crew751_cursor_is_the_hermes_worker.py); it is never the primary
model here. This file locks hermes-v2's own contract so a merge cannot swap the phone brain first.

Rescued from the 2026-09-03 dirty tree (rescue/2026-09-03/hermes-v2) and adapted: the primary
lane is the founder's to choose (claude until 2026-09-02, kimi since), so the rule is "a router
lane on a router key", not one lane's name; and estate.yaml is not a tracked file of this repo
(the live one is idp platform/hermes-agent/estate.yaml), so it is graded there, not here.
"""

# ruff: noqa: S101
import os

import yaml

HOME = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(HOME, "config.yaml")
DOCKERFILE = os.path.join(HOME, "Dockerfile")
PLUGIN = os.path.join(HOME, "plugins", "model-providers", "cursor-acp", "plugin.yaml")


def _cfg():
    with open(CONFIG) as f:
        return yaml.safe_load(f)


def test_architect_primary_is_a_router_lane_on_a_router_key():
    cfg = _cfg()
    for block in (cfg["model"], cfg["aux"]):
        assert block["provider"] == "custom"
        assert block["base_url"].startswith("https://llm.")
        assert block["key_env"] == "LITELLM_API_KEY"
    assert cfg["model"]["default"], (
        "the primary lane is named, whichever the founder chose"
    )


def test_architect_is_not_cursor_acp():
    cfg = _cfg()
    assert cfg["model"]["provider"] != "cursor-acp"
    hops = cfg.get("fallback_providers") or []
    assert all(h.get("provider") != "cursor-acp" for h in hops)
    assert not os.path.isfile(PLUGIN)


def test_dockerfile_does_not_pipe_an_unpinned_cursor_installer():
    with open(DOCKERFILE) as f:
        text = f.read()
    assert "cursor.com/install" not in text
    assert "CURSOR_CLI_HOME" not in text
