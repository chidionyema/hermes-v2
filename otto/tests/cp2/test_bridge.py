"""Spec step 1: the fork-world self bridged into the tool gateway.

Proves (spec Done line):
  - at least 30 tools are registered from a stub ``get_tool_definitions``
    even when the fork is absent from the checkout (the stub replaces
    ``model_tools`` in ``sys.modules``; a bare checkout has no fork to
    import, which is exactly the failure this file guards against);
  - a ``terminal`` call whose ``command`` carries ``rm -rf /`` is refused on
    the plain T2 path (never executes) and the destructive intent routes to
    ``terminal_irreversible``, which is T3 + irreversible and therefore
    reaches the human gate;
  - an unknown tool name comes back as ``DenialReason.UNKNOWN_TOOL``.
"""

from __future__ import annotations

import sys
import types
import uuid

import pytest

from otto.gateway.bridge import (
    IRREVERSIBLE_TERMINAL,
    TERMINAL,
    TERMINAL_IRREVERSIBLE,
    ForkToolDenied,
    register_fork_tools,
)
from otto.gateway.config import GatewayConfig
from otto.gateway.core import (
    ApprovalToken,
    Envelope,
    ToolGateway,
    fail_closed_gate,
)
from otto.gateway.registry import Tier, ToolRegistry

# A hand-built fork definition adequate for tier resolution and the spec's
# ≥30 count. Real toolsets are named after the deployment default
# (terminal, web, search, skills, file, vision, tts, memory, todo, cronjob,
# code_execution); names here are chosen so the bridge's tier map places
# them where the assertions expect.
_WEB = [f"web_fetch_{i}" for i in range(10)]
_SEARCH = [f"search_query_{i}" for i in range(6)]
_VISION = [f"vision_analyze_{i}" for i in range(5)]
_WRITE = [
    "write_file",
    "patch",
    "code_execution",
    "cronjob_schedule",
    "todo_add",
    "tts_speak",
    "terminal",
]
_SKILLS = ["skills_run_docker", "skills_run_k8s"]
_MEMORY = ["memory_read_notes", "memory_write_note"]
_MCP_READ = ["estate_find_entity", "ask_holmes_query"]
_BASIC = ["read_file", "session_search"]

_ALL = _WEB + _SEARCH + _VISION + _SKILLS + _MEMORY + _MCP_READ + _BASIC + _WRITE


def _defs(names: list[str]) -> list[dict]:
    return [
        {
            "name": n,
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}}
                if n == "terminal"
                else {"query": {"type": "string"}},
                "required": ["command"] if n == "terminal" else ["query"],
            },
        }
        for n in names
    ]


@pytest.fixture
def stub_fork(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Monkeypatch a minimal fork module so ``register_fork_tools`` can
    import ``model_tools`` without the real fork present."""

    calls = []

    def get_tool_definitions(*, enabled_toolsets: list[str]) -> list[dict]:
        return _defs(_ALL)

    def handle_function_call(name: str, args: dict, task_id: str = "") -> str:
        calls.append((name, dict(args), task_id))
        return f'{{"ran": {name!r}}}'

    mod = types.ModuleType("model_tools")
    mod.get_tool_definitions = get_tool_definitions
    mod.handle_function_call = handle_function_call
    monkeypatch.setitem(sys.modules, "model_tools", mod)
    return calls


@pytest.fixture
def registry(stub_fork: list[str]):
    reg = ToolRegistry(config=GatewayConfig(max_tools=200))
    register_fork_tools(reg, enabled_toolsets=["terminal"])
    return reg


def test_registers_at_least_30_tools(registry: ToolRegistry) -> None:
    assert len(registry) >= 30
    assert TERMINAL in registry
    assert TERMINAL_IRREVERSIBLE in registry


def test_terminal_is_t2_terminal_irreversible_is_t3(registry: ToolRegistry) -> None:
    assert registry.get(TERMINAL).tier is Tier.T2
    assert registry.get(TERMINAL).irreversible is False
    t3 = registry.get(TERMINAL_IRREVERSIBLE)
    assert t3.tier is Tier.T3
    assert t3.irreversible is True


def test_read_only_tools_are_t1(registry: ToolRegistry) -> None:
    assert registry.get("read_file").tier is Tier.T1
    assert registry.get("session_search").tier is Tier.T1
    # estate + ask_holmes MCP reads are visible and T1
    assert registry.get("estate_find_entity").tier is Tier.T1


def test_terminal_rm_rf_refused_on_t2_path(registry: ToolRegistry) -> None:
    gateway = ToolGateway(registry=registry)
    env = Envelope(task_id="t1", authority_ceiling=Tier.T2)
    with pytest.raises(ForkToolDenied):
        gateway.call(env, TERMINAL, {"command": "rm -rf /"})


def test_destructive_command_reaches_human_gate_at_t3(registry: ToolRegistry) -> None:
    gateway = ToolGateway(registry=registry)
    env = Envelope(task_id="t2", authority_ceiling=Tier.T3)

    # Gate present but declines -> the T3+irreversible call reached the hook
    # and was refused (HUMAN_APPROVAL_REFUSED); it never silently executes.
    gateway.human_gate = lambda envelope, tool: None
    resp = gateway.call(env, TERMINAL_IRREVERSIBLE, {"command": "rm -rf /"})
    assert resp.denied
    assert resp.denial.reason == "HUMAN_APPROVAL_REFUSED"

    # Gate approves -> the call executes (it reached the gate and cleared it).
    # The token is a test double, not a credential (ruff S106 fires on the
    # field name "token" alone), so it is randomised like the cp2 step_defs.
    approval = "approval-" + uuid.uuid4().hex[:8]
    gateway.human_gate = lambda envelope, tool: ApprovalToken(
        token=approval, approved_by="founder"
    )
    resp2 = gateway.call(env, TERMINAL_IRREVERSIBLE, {"command": "rm -rf /"})
    assert resp2.ok

    # Gate untouched -> the T3+irreversible spec still demands one.
    gateway.human_gate = None
    resp3 = gateway.call(env, TERMINAL_IRREVERSIBLE, {"command": "rm -rf /"})
    assert resp3.denied
    assert resp3.denial.reason == "HUMAN_APPROVAL_REQUIRED"


def test_irreversible_regex_matches_toxic_commands() -> None:
    for lethal in (
        "git push --force origin main",
        "git push --force-with-lease",
        "rm -rf /",
        "kubectl delete pod attacker",
        "DROP TABLE users;",
        "terraform destroy",
    ):
        assert IRREVERSIBLE_TERMINAL.search(lethal), lethal
    for benign in ("git log", "ls -la", "kubectl get pods", "echo hi"):
        assert not IRREVERSIBLE_TERMINAL.search(benign), benign


def test_unknown_tool_is_unknown_tool_reason(registry: ToolRegistry) -> None:
    gateway = ToolGateway(registry=registry)
    env = Envelope(task_id="t3", authority_ceiling=Tier.T3)
    resp = gateway.call(env, "no_such_tool", {})
    assert resp.denied
    assert resp.denial.reason == "UNKNOWN_TOOL"


def test_nested_function_shape_registers(monkeypatch: pytest.MonkeyPatch) -> None:
    """A definition in the OpenAI function shape
    ({"type": "function", "function": {..., "name": ...}}) bridges exactly
    like the flat fork shape — name, description and parameters are read from
    the nested body, not lost."""

    def get_tool_definitions(*, enabled_toolsets: list[str]) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "todo_add",
                    "description": "Append one task to the todo list.",
                    "parameters": {
                        "type": "object",
                        "properties": {"text": {"type": "string"}},
                        "required": ["text"],
                    },
                },
            },
            # estate MCP arrives nested too; stays a T1 read.
            {
                "type": "function",
                "function": {
                    "name": "estate_find_entity",
                    "description": "Look up one estate entity.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        ]

    def handle_function_call(name: str, args: dict, task_id: str = "") -> str:
        return f'{{"ran": {name!r}}}'

    mod = types.ModuleType("model_tools")
    mod.get_tool_definitions = get_tool_definitions
    mod.handle_function_call = handle_function_call
    monkeypatch.setitem(sys.modules, "model_tools", mod)

    reg = ToolRegistry(config=GatewayConfig(max_tools=200))
    register_fork_tools(reg, enabled_toolsets=["todo", "estate"])
    assert "todo_add" in reg
    assert reg.get("todo_add").tier is Tier.T2
    assert reg.get("todo_add").description == "Append one task to the todo list."
    assert "estate_find_entity" in reg
    assert reg.get("estate_find_entity").tier is Tier.T1


def test_build_registry_refuses_when_toolset_env_gives_zero_fork_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When OTTO_TOOLSETS names a surface but the fork returns no tools,
    building the registry refuses loudly (BootRefused) instead of booting a
    gateway that has no hands — a silent tool-less boot is worse than none.
    """
    from otto.boot.errors import BootRefused
    from otto.boot.pipeline import build_registry

    def get_tool_definitions(*, enabled_toolsets: list[str]) -> list[dict]:
        return []

    def handle_function_call(name: str, args: dict, task_id: str = "") -> str:
        return '{"result": "ok"}'

    mod = types.ModuleType("model_tools")
    mod.get_tool_definitions = get_tool_definitions
    mod.handle_function_call = handle_function_call
    monkeypatch.setitem(sys.modules, "model_tools", mod)
    monkeypatch.setenv("OTTO_TOOLSETS", "terminal")

    with pytest.raises(BootRefused):
        build_registry()


def test_tool_schema_never_ships_an_empty_description() -> None:
    """A tool registered without a description still reaches the model with
    a non-empty one — built from its name rather than left blank. A model
    told a tool exists but not what it does tends to guess it, which is how
    a wrong tool gets called."""
    from otto.boot.pipeline import _tool_schema
    from otto.gateway.registry import ToolSpec

    spec = ToolSpec(
        name="read_session_notes",
        tier=Tier.T1,
        input_schema={"type": "object", "properties": {}},
    )
    fn = _tool_schema(spec)["function"]
    assert fn["name"] == "read_session_notes"
    assert fn["description"]  # non-empty, not an empty string

    # An explicit description is carried through untouched.
    named = ToolSpec(
        name="todo_add",
        tier=Tier.T2,
        input_schema={"type": "object", "properties": {}},
        description="Append one task to the list.",
    )
    assert _tool_schema(named)["function"]["description"] == (
        "Append one task to the list."
    )


def test_fail_closed_gate_emits_human_gate_observability(
    registry: ToolRegistry,
) -> None:
    """The default ``fail_closed_gate`` refuses every human-gated call and
    the answering lane's executor turns that into the proof row's
    ``gateway.denied ... reason=human_gate`` observability line, while the
    gateway's structured denial keeps ``HUMAN_APPROVAL_REFUSED``.

    This is the contract behind the founder proof row
    ("delete the otto-gateway deployment" -> ``reason=human_gate``): a real
    gate is wired at boot/worker, it returns no token for a T3/irreversible
    call, and nothing runs --- but the operator can grep the door log for the
    exact reason the spec names.
    """
    from contextlib import nullcontext as _nullcontext

    from otto.boot.pipeline import _build_tool_loop
    from otto.gateway.registry import Tier as GatewayTier
    from otto.obs.core import TaskContext

    class Recording:
        def __init__(self) -> None:
            self.calls: list[tuple] = []

        def info(self, event: str, ctx: TaskContext, **fields: object) -> None:
            self.calls.append((event, dict(fields)))

        def task_span(self, *_a: object, **_k: object):
            return _nullcontext()

    router = Recording()
    lane = Recording()
    obs = type("Obs", (), {"router": router, "gateway": lane})()

    gateway = ToolGateway(registry=registry, human_gate=fail_closed_gate)
    tools, executor = _build_tool_loop(
        ceiling=GatewayTier.T3,
        registry_gateway=gateway,
        obs=obs,
        ctx=TaskContext(task_ulid="task-gate-1"),
    )

    outcome = executor(TERMINAL_IRREVERSIBLE, '{"command": "rm -rf /"}')

    # The tool-turn line reports the gateway's structured reason.
    assert any(
        e == "router.tool_turn"
        and f.get("denied") is True
        and f.get("reason") == "HUMAN_APPROVAL_REFUSED"
        for e, f in router.calls
    )
    # The door log's human-gate line reads exactly what the proof row greps.
    assert any(
        e == "gateway.denied"
        and f.get("reason") == "human_gate"
        and f.get("tool") == TERMINAL_IRREVERSIBLE
        for e, f in lane.calls
    )
    # Nothing ran, and the model was told the structured reason.
    assert outcome == "denied: HUMAN_APPROVAL_REFUSED"
