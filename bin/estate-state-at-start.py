#!/usr/bin/env python3
"""Read the estate's state through the estate MCP once at boot (crew#561, founder 2026-08-30:
"Otto need to be mandated also else may forget").

Prints exactly one line to the gateway log: `estate-state: READ <generated_at> sha=<12>` or
`estate-state: BLIND <reason>`. The otto-parity drill greps that line, so a silent boot is a
red row, not a green one. Exit is always 0: a blind door must not take the gateway down.
A receipt (generated_at and hash) is written beside the state; the document itself is not
copied, the MCP stays the one store.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import sys
import urllib.request

import yaml

HOME = pathlib.Path(os.environ.get("HERMES_HOME", "/data"))
KEY = os.environ.get("ESTATE_MCP_KEY", "")
RECEIPT = HOME / "estate-state.receipt"


def rpc(url: str, sid: str | None, method: str, params: dict, rid: int):
    headers = {
        "Authorization": "Bearer " + KEY,
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if sid:
        headers["Mcp-Session-Id"] = sid
    body = json.dumps(
        {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
    ).encode()
    r = urllib.request.urlopen(urllib.request.Request(url, body, headers), timeout=20)
    raw = r.read().decode()
    if raw.lstrip().startswith("event:") or "\ndata:" in raw or raw.startswith("data:"):
        raw = "".join(line[5:] for line in raw.splitlines() if line.startswith("data:"))
    return json.loads(raw), r.headers.get("Mcp-Session-Id")


def main() -> int:
    try:
        cfg = yaml.safe_load((HOME / "config.yaml").read_text(encoding="utf-8"))
        url = cfg["mcp_servers"]["estate"]["url"]
        if not KEY:
            raise RuntimeError("ESTATE_MCP_KEY is not in the env dir")
        init = {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "otto-boot", "version": "0"},
        }
        _, sid = rpc(url, None, "initialize", init, 1)
        res, _ = rpc(
            url, sid, "tools/call", {"name": "get_estate_state", "arguments": {}}, 2
        )
        content = res["result"]["content"][0]["text"]
        doc = json.loads(content)
        doc = doc.get("document", doc)
        when = doc.get("generated_at", "unknown")
        sha = hashlib.sha256(content.encode()).hexdigest()[:12]
        RECEIPT.write_text(json.dumps({"generated_at": when, "sha": sha}) + "\n")
        print(f"estate-state: READ {when} sha={sha}")
    except Exception as e:  # noqa: BLE001 - the one line is the finding
        RECEIPT.unlink(missing_ok=True)
        print(f"estate-state: BLIND {type(e).__name__}: {e}")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
