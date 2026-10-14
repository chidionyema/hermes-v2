"""The estate's unified memory, reached through the agentgateway's MCP endpoint.

Every other agent on the estate already shares one memory: Claude Code and the
other runtimes on the operator's Mac through the laptop bridge, and the two
hermes-image Ottos (hermes-agent-gateway, otto-gateway) through the
``mcp_servers`` entry in config.yaml. otto.boot has no MCP client, so @numun_bot
answered from its own hindsight bank and fast_recall store and never saw what
the rest of the estate had decided. This module is the smallest client that
closes that gap: three tools, stdlib only, the same as ``hindsight``.

    brief(task)        what to know before answering    (on the answering path)
    recall(query)      what is already known about this (on demand)
    remember(content)  what just happened               (after the answer)

The protocol is MCP over streamable HTTP: one ``initialize`` POST whose
response carries an ``mcp-session-id`` header, then ``tools/call`` with that
header. A response may be a JSON body or an SSE stream; for SSE the last
``data:`` line is the message. A tool's result is JSON text in
``result.content[0].text``, and ``result.isError`` marks a tool failure.

Configuration is two settings, both supplied by the idp infra (the key is the
gateway's MCP_GATEWAY_KEY from vault entry ``mcp-gateway``, mounted as a file):

    ESTATE_MCP_URL       e.g. http://agentgateway.mcp.svc.cluster.local:3000/estate/mcp
    ESTATE_MCP_KEY_FILE  path to the file holding the bearer key

Either unset, empty or unreadable and every call is a no-op that returns
nothing, so a pod without the infra behaves exactly as it did before. Never
raises: shared memory that cannot be reached must never cost the sender their
answer, and every call is bounded by a deadline that covers both round trips.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

_LOG = logging.getLogger(__name__)

URL_ENV = "ESTATE_MCP_URL"
KEY_FILE_ENV = "ESTATE_MCP_KEY_FILE"

#: Who is writing, as the server's ``remember`` documents the field.
RUNTIME = "otto-numun"
SOURCE = "telegram"

PROTOCOL_VERSION = "2025-03-26"

#: brief sits on the answering path, so its whole budget -- initialize and the
#: call together -- is what a person waiting in a chat should not notice.
BRIEF_TIMEOUT_S = 3.0
RECALL_TIMEOUT_S = 3.0
#: remember runs off the answering path (remember_in_background).
REMEMBER_TIMEOUT_S = 5.0
#: Prompt budget, not a dump: the model has to read what comes back.
BRIEF_LIMIT = 5
BRIEF_MAX_CHARS = 2400


@dataclass(frozen=True)
class EstateMemoryConfig:
    url: str | None
    key: str | None

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.key)


def config(env: dict[str, str] | None = None) -> EstateMemoryConfig:
    """Read both settings. The key is read from its file on every call, so a
    rotated secret is picked up without a restart. A URL that is not http(s)
    is treated as unset, for the same reason hindsight.config refuses one."""
    src = env if env is not None else os.environ
    url = (src.get(URL_ENV) or "").strip() or None
    if url and not url.startswith(("http://", "https://")):
        _LOG.warning("estate_memory.bad_scheme: %s is not an http(s) URL; off", URL_ENV)
        url = None
    key = None
    path = (src.get(KEY_FILE_ENV) or "").strip()
    if url and path:
        try:
            with open(path, encoding="utf-8") as fh:
                key = fh.read().strip() or None
        except OSError as exc:
            _LOG.warning(
                "estate_memory.key_unreadable %s error=%s", KEY_FILE_ENV, type(exc).__name__
            )
    return EstateMemoryConfig(url=url, key=key)


def _parse_body(raw: bytes) -> dict[str, Any] | None:
    """A JSON body, or an SSE stream whose last ``data:`` line is the message."""
    text = raw.decode("utf-8", errors="replace")
    data_lines = [ln[5:].strip() for ln in text.splitlines() if ln.startswith("data:")]
    if data_lines:
        text = data_lines[-1]
    if not text.strip():
        return {}
    try:
        parsed = json.loads(text)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _post(
    cfg: EstateMemoryConfig,
    payload: dict[str, Any],
    *,
    session_id: str | None,
    deadline: float,
) -> tuple[str | None, dict[str, Any] | None]:
    """One JSON-RPC POST. Returns (session id header, parsed message)."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("estate memory deadline spent")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {cfg.key}",
    }
    if session_id:
        headers["mcp-session-id"] = session_id
    req = urllib.request.Request(  # noqa: S310 - config() refuses any scheme but http(s)
        cfg.url or "", data=json.dumps(payload).encode("utf-8"), method="POST", headers=headers
    )
    with urllib.request.urlopen(req, timeout=remaining) as resp:  # noqa: S310 - fixed http(s) endpoint from config
        return resp.headers.get("mcp-session-id"), _parse_body(resp.read())


def _call_tool(
    name: str, arguments: dict[str, Any], *, timeout: float, cfg: EstateMemoryConfig
) -> dict[str, Any] | None:
    """initialize, then tools/call; the tool's JSON result, or None on any
    failure. Deliberately never raises: see the module docstring."""
    deadline = time.monotonic() + timeout
    try:
        session_id, init = _post(
            cfg,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": RUNTIME, "version": "1"},
                },
            },
            session_id=None,
            deadline=deadline,
        )
        if init is None or "error" in init:
            _LOG.warning("estate_memory.initialize_refused tool=%s", name)
            return None
        _, msg = _post(
            cfg,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
            session_id=session_id,
            deadline=deadline,
        )
    except (urllib.error.URLError, OSError, TimeoutError, ValueError) as exc:
        _LOG.warning("estate_memory.unreachable tool=%s error=%s", name, type(exc).__name__)
        return None
    if not msg or "error" in msg:
        _LOG.warning("estate_memory.rpc_error tool=%s", name)
        return None
    result = msg.get("result") or {}
    if result.get("isError"):
        _LOG.warning("estate_memory.tool_error tool=%s", name)
        return None
    try:
        text = result["content"][0]["text"]
        parsed = json.loads(text)
    except (KeyError, IndexError, TypeError, ValueError):
        _LOG.warning("estate_memory.unparseable tool=%s", name)
        return None
    return parsed if isinstance(parsed, dict) else None


def _render(items: Any, seen: set[str]) -> list[str]:
    lines = []
    if isinstance(items, list):
        for item in items:
            text = str(item.get("text") or "").strip() if isinstance(item, dict) else ""
            if text and text not in seen:
                seen.add(text)
                lines.append(f"- {text}")
    return lines


def brief(task: str, *, cfg: EstateMemoryConfig | None = None) -> str:
    """What the estate's agents already know that bears on ``task``.

    Prompt-ready text, empty when shared memory is off, unreachable or has
    nothing. ``relevant`` first, then the newest decisions and incidents from
    ``recent``; capped so it never crowds the message out of the prompt.
    """
    cfg = cfg or config()
    if not cfg.enabled or not task.strip():
        return ""
    data = _call_tool("brief", {"task": task, "limit": BRIEF_LIMIT}, timeout=BRIEF_TIMEOUT_S, cfg=cfg)
    if not data:
        return ""
    seen: set[str] = set()
    lines = _render(data.get("relevant"), seen) + _render(data.get("recent"), seen)
    return "\n".join(lines)[:BRIEF_MAX_CHARS].strip()


def recall(
    query: str,
    *,
    limit: int = 5,
    subject: str = "",
    kind: str = "",
    cfg: EstateMemoryConfig | None = None,
) -> list[dict[str, Any]]:
    """The memories matching ``query``, as the server returns them
    ({"text", "metadata"}); empty when off, unreachable or nothing matched."""
    cfg = cfg or config()
    if not cfg.enabled:
        return []
    args: dict[str, Any] = {"query": query, "limit": limit}
    if subject:
        args["subject"] = subject
    if kind:
        args["kind"] = kind
    data = _call_tool("recall", args, timeout=RECALL_TIMEOUT_S, cfg=cfg)
    memories = (data or {}).get("memories")
    return [m for m in memories if isinstance(m, dict)] if isinstance(memories, list) else []


def remember(
    content: str,
    *,
    subject: str = "",
    kind: str = "fact",
    cfg: EstateMemoryConfig | None = None,
) -> bool:
    """Write one thing that happened into the estate's shared memory. True
    when the server accepted it."""
    cfg = cfg or config()
    if not cfg.enabled or not content.strip():
        return False
    args: dict[str, Any] = {
        "content": content,
        "kind": kind,
        "source": SOURCE,
        "runtime": RUNTIME,
    }
    if subject:
        args["subject"] = subject
    return _call_tool("remember", args, timeout=REMEMBER_TIMEOUT_S, cfg=cfg) is not None


def remember_in_background(
    content: str, *, subject: str = "", kind: str = "fact"
) -> threading.Thread | None:
    """remember() on a daemon thread, so the answer is never held up by the
    write. None when shared memory is off (no thread is started)."""
    cfg = config()
    if not cfg.enabled or not content.strip():
        return None
    thread = threading.Thread(
        target=remember,
        args=(content,),
        kwargs={"subject": subject, "kind": kind, "cfg": cfg},
        name="estate-memory-remember",
        daemon=True,
    )
    thread.start()
    return thread
