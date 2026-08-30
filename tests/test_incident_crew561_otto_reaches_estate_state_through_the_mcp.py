"""crew#561 CP3: Otto reads estate state through the estate MCP, never from a copy of its own.

Incident, 2026-08-30: config.yaml named no MCP server, so every "state of the estate" answer
from Telegram was a search or a memory. The key is a placeholder the runtime resolves from the
pod's env dir; a literal token in this file is the laptop-paste habit and is refused here.
"""
from __future__ import annotations

import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_incident_crew561_estate_mcp_is_configured_with_a_placeholder_key():
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    estate = cfg["mcp_servers"]["estate"]
    assert estate["url"].endswith("/estate/mcp") and estate["url"].startswith("https://")
    auth = estate["headers"]["Authorization"]
    assert auth == "Bearer ${ESTATE_MCP_KEY}", auth
