"""crew#751: Architect stays on the estate router until WORK has a real PR.

ACP as the Telegram brain was the invasive path (fork patches, unknown-method hang,
no stream). The official Cursor SDK can back a local OpenAI /v1 later; that is CP2
and it does not start until CP1 (Mac `agent -p`) has opened an agent-go PR. This
file locks the current contract so a merge cannot swap the phone brain first.
"""
import os

import yaml

HOME = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(HOME, "config.yaml")
DOCKERFILE = os.path.join(HOME, "Dockerfile")
ESTATE = os.path.join(HOME, "estate.yaml")
PLUGIN = os.path.join(HOME, "plugins", "model-providers", "cursor-acp", "plugin.yaml")


def test_architect_primary_is_the_router_claude_lane():
    cfg = yaml.safe_load(open(CONFIG))
    assert cfg["model"]["provider"] == "custom"
    assert cfg["model"]["default"] == "claude"
    assert "llm." in cfg["model"]["base_url"]
    assert cfg["model"]["key_env"] == "LITELLM_API_KEY"
    assert cfg["aux"]["provider"] == "custom"
    assert cfg["aux"]["model"] == "claude-fast"


def test_architect_is_not_cursor_acp():
    cfg = yaml.safe_load(open(CONFIG))
    assert cfg["model"]["provider"] != "cursor-acp"
    hops = cfg.get("fallback_providers") or []
    assert all(h.get("provider") != "cursor-acp" for h in hops)
    assert not os.path.isfile(PLUGIN)


def test_dockerfile_does_not_pipe_an_unpinned_cursor_installer():
    text = open(DOCKERFILE).read()
    assert "cursor.com/install" not in text
    assert "CURSOR_CLI_HOME" not in text


def test_this_estate_yaml_is_the_mac_ancestor_not_the_cluster():
    text = open(ESTATE).read()
    assert "idp/platform/hermes-agent/estate.yaml" in text
    doc = yaml.safe_load(text)
    assert str(doc["features"]["work"]).lower() in {"off", "false"}
    assert doc["dispatch"]["runtime"] != "cursor"
