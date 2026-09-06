"""The fork-world self, bridged into the tool gateway (spec step 1).

hermes-v2 hands the fork (`hermes-agent`) every capability it already owns:
terminal, web, search, skills, file, vision, tts, memory, todo, cronjob,
code_execution — 35 toolsets' worth (``model_tools.get_tool_definitions``).
The fork mounts in the same image (installed under ``/app``), but it is git
ignored in a bare checkout and only materialises at image build, so the
import of ``model_tools`` here is deferred behind a function call — unit
tests without the fork still run (``otto.gateway`` boot already defers its
lane imports the same way).

The one thing this file does is put those definitions behind the gateway's
real classes (``ToolRegistry``/``ToolSpec``) so the invariants already
enforced by ``ToolGateway.call`` apply unchanged:

- authority is tiered, never advisory (P2) — a tool's ``tier`` here is what
  the gateway compares against the envelope's effective ceiling;
- everything that cannot be undone rides ``tier=T3`` with
  ``irreversible=True`` so the human gate fires for it even outside the
  T3 set (P7, ``GatewayConfig.human_gate_tiers``);
- a read-only tool is never granted write authority by the map.

The tier map mirrors `idp/AGENTS.md` ``[capabilities] destructive``
(``fs_delete``, ``git_push_force``, ``db_drop``, ``service_destroy``),
widened to the shapes a *command* can carry — the estate-observed
``kubectl delete``, ``terraform destroy`` and ``rm -rf`` that no capability
tag can cover because they arrive inside a ``terminal`` string, not as
their own tool name. A destructive ``terminal`` call never runs through the
plain T2 path: the bridge registers a synthetic ``terminal_irreversible``
spec (T3 + irreversible) and its T2 twin refuses any matching command so
not even an un-routed call executes it.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from otto.gateway.registry import Tier, ToolRegistry, ToolSpec

TERMINAL = "terminal"
TERMINAL_IRREVERSIBLE = "terminal_irreversible"

# A command that cannot be undone. {token} picks the deciding words; the
# estate treats `git push --force`, `rm -rf`, `kubectl delete`, SQL
# `drop table|database` and `terraform destroy` as un-undoable regardless of
# which toolset produced them.
IRREVERSIBLE_TERMINAL = re.compile(
    r"(?:"
    r"git\s+push\s+(?:--force|--force-with-lease|--delete)|"
    r"rm\s+-r[f]?\b|"
    r"\bkubectl\s+delete\b|"
    r"\bdrop\s+(?:table|database)\b|"
    r"\bterraform\s+destroy\b"
    r")",
    re.IGNORECASE,
)

# Tool name -> tier, with a name-prefix fallback so a toolset stays coherent
# when the fork adds a tool whose exact name was not in the map when this
# shipped. T1 (read-only estate self), T2 (write / shell), and any tool that
# matches neither is refused entry at T2 by default — never silently T1.
_T1_PREFIX = ("web_", "search_", "vision_", "estate", "ask_holmes")
_T1_NAME = ("read_file", "session_search")
_T2_NAME = ("write_file", "patch", "code_execution", "cronjob", "todo", "tts")
_T2_PREFIX = ("skills_",)


def _toolset(name: str) -> str:
    """The coarse toolset a tool name belongs to (for the fallback rule)."""
    if name == TERMINAL:
        return "terminal"
    if name.startswith("skill"):
        return "skills"
    if name.startswith(("web_", "search_", "read_", "memory_read", "session")):
        return "web"
    if name.startswith("file"):
        return "file"
    if name.startswith(("vision", "image")):
        return "vision"
    if name.startswith("tts"):
        return "tts"
    if name.startswith("memory"):
        return "memory"
    if name.startswith("todo"):
        return "todo"
    if name.startswith("cron"):
        return "cronjob"
    if name.startswith("code"):
        return "code_execution"
    return "tool"


def _tier_for(name: str, toolset: str) -> Tier:
    """Resolve a fork tool's tier by name — T1 if read-only, else T2.

    ``memory`` reads vs writes share the name space in the fork; the bridge
    grants the whole memory surface T2 (a memory *write* is a real mutation,
    and a read reaching a store is only "read" at the agent boundary).  Every
    MCP tool whose name begins ``estate`` or ``ask_holmes`` is read-only.
    """
    if name.startswith(_T1_PREFIX) or name in _T1_NAME:
        return Tier.T1
    if toolset in ("web", "search", "vision"):
        return Tier.T1  # generic web/vision reads
    if name in _T2_NAME or name == TERMINAL or name.startswith(_T2_PREFIX):
        return Tier.T2
    # Default: an unfamiliar tool is T2 — write-capable default, refused to
    # any envelope whose effective ceiling is T1 or below.
    return Tier.T2


def _model_tools() -> Any:
    """Lazily import the fork's model bridge (see module docstring)."""
    import model_tools  # type: ignore[import-not-found]

    return model_tools


def register_fork_tools(registry: ToolRegistry, *, enabled_toolsets: list[str]) -> int:
    """Register the enabled fork tools behind ``registry``; return the count.

    ``terminal`` is T2 and executes ordinary (non-destructive) commands.  A
    command matching ``IRREVERSIBLE_TERMINAL`` is never executed on that T2
    path — the spec's loop is expected to call ``terminal_irreversible`` for
    the destructive case the agent is actually serious about, and that spec
    is T3 with ``irreversible=True`` so ``ToolGateway.call`` raises through
    the human gate (P7).
    """
    mt = _model_tools()
    definitions = mt.get_tool_definitions(enabled_toolsets=enabled_toolsets)

    registered = 0
    for definition in definitions:
        name = definition.get("name")
        if not name or name in registry.names():
            continue  # ``note`` and friends are already registered; never dup
        toolset = _toolset(name)
        tier = _tier_for(name, toolset)
        schema = _schema(definition)
        if name == TERMINAL:
            # Register the per-call gated spec alongside the T2 one.
            spec = ToolSpec(
                name=name,
                tier=tier,
                input_schema=schema,
                handler=_terminal_handler(),
                idempotent=False,
            )
        else:
            spec = ToolSpec(
                name=name,
                tier=tier,
                input_schema=schema,
                handler=_fork_handler(name),
                irreversible=False,
                idempotent=True,
            )
        registry.register(spec)
        registered += 1

    # The synthetic T3 spec: destructive-terminal calls route here. The
    # human gate fires on T3 or ``irreversible``, so this never auto-runs.
    if "terminal_irreversible" not in registry.names():
        registry.register(
            ToolSpec(
                name=TERMINAL_IRREVERSIBLE,
                tier=Tier.T3,
                input_schema={
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "The destructive command; executed only after a human approval token.",
                        }
                    },
                    "required": ["command"],
                },
                handler=_terminal_irreversible_handler(),
                irreversible=True,
                idempotent=False,
            )
        )
        registered += 1
    return registered


def _schema(definition: dict[str, Any]) -> dict[str, Any]:
    """The fork's input schema, or an empty object shape if absent."""
    schema = definition.get("parameters") or definition.get("input_schema")
    if isinstance(schema, dict):
        return schema
    return {"type": "object", "properties": {}, "required": []}


def _run_fork(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Execute one fork tool and return the ``{"result": <string>}`` shape.

    The fork's ``handle_function_call`` returns a JSON string; the bridge
    never raises it away — a string body comes back untouched, a non-string
    is stringified so the contract stays ``{"result": str}``.
    """
    mt = _model_tools()
    raw = mt.handle_function_call(name, args)
    if not isinstance(raw, str):
        raw = str(raw)
    return {"result": raw}


def _terminal_handler() -> Callable[[dict[str, Any]], dict[str, Any]]:
    """T2 terminal. Executes ordinary commands; hard-refuses a destructive one."""

    def _handle(args: dict[str, Any]) -> dict[str, Any]:
        command = str(args.get("command") or "")
        if IRREVERSIBLE_TERMINAL.search(command):
            raise ForkToolDenied(
                f"command {command!r} matched the un-undoable set; route it to "
                f"{TERMINAL_IRREVERSIBLE} (T3) for the human gate."
            )
        return _run_fork(TERMINAL, args)

    return _handle


def _terminal_irreversible_handler() -> Callable[[dict[str, Any]], dict[str, Any]]:
    """T3 + irreversible terminal body. Reached only through the human gate."""

    def _handle(args: dict[str, Any]) -> dict[str, Any]:
        # Executes the (approved) destructive command: still routed through
        # the fork's own terminal underneath the T3 name/tier.
        return _run_fork(TERMINAL, args)

    return _handle


def _fork_handler(name: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Handler for every non-terminal fork tool."""

    def _handle(args: dict[str, Any]) -> dict[str, Any]:
        return _run_fork(name, args)

    return _handle


class ForkToolDenied(RuntimeError):
    """The plain (T2) terminal path refused a destructive command.

    Distinct from a gateway denial: this is a *handler-side* refusal before
    execution, so the un-undoable command never runs even if the loop failed
    to route it to ``terminal_irreversible``. ``otto.gateway.errors`` keeps
    shape errors; this one is about an executable command's contents, which
    is why it lives here rather than being smuggled into the schema layer.
    """
