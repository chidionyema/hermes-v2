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
    assert estate["url"].endswith("/estate/mcp") and estate["url"].startswith(
        "https://"
    )
    auth = estate["headers"]["Authorization"]
    assert auth == "Bearer ${ESTATE_MCP_KEY}", auth


def test_incident_crew561_the_read_is_mandated_in_the_soul_and_at_boot():
    """Founder, 2026-08-30: "Otto need to be mandated also else may forget". The mandate is in
    the system prompt every turn (SOUL.md) and in the boot path (entrypoint), and a session that
    cannot reach the door says BLIND rather than remembering."""
    soul = (ROOT / "SOUL.md").read_text(encoding="utf-8")
    assert "`get_estate_state`" in soul and "mandatory" in soul, (
        "SOUL.md does not mandate the read"
    )
    assert "`BLIND:`" in soul, "SOUL.md does not say what a failed read looks like"
    assert "STATE.md, rebuilt hourly" not in soul, (
        "SOUL.md still points Otto at the laptop file"
    )
    entry = (ROOT / "deploy" / "k8s" / "entrypoint.sh").read_text(encoding="utf-8")
    assert "bin/estate-state-at-start.py" in entry, (
        "the entrypoint does not read estate state"
    )
    assert entry.index("bin/estate-state-at-start.py") < entry.index("gateway run"), (
        "the read is not before the gateway"
    )
    script = (ROOT / "bin" / "estate-state-at-start.py").read_text(encoding="utf-8")
    assert (
        "get_estate_state" in script
        and "estate-state: BLIND" in script
        and "estate-state: READ" in script
    )
    assert "return 0" in script, "a blind door must not take the gateway down"
