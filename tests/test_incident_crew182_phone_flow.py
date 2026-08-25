"""crew#182 Package C: the phone idea flow is isolated and vendor-free.

idp/docs/specs/phone-idea-flow.md, checkpoints CP1, CP2, CP12, CP13. Founder,
2026-08-26: "deliver the full spec, get it operational asap, divide and conquer".

The incidents these guard. CP1/CP2: the founder's laptop session (Claude Code or
any runtime) was interrupted or polluted by work that arrived from the phone;
a Telegram message must never read, write or share a process with it. CP12/CP13:
the flow was once specified on one vendor's remote channel; the model behind
hermes-v2 is chosen by `config.yaml` `model.provider`, never by code, and no
flow module imports a model vendor's client.

Rung 4, incident tests, named for the checkpoint. Each proves both ways: the
guard sees what it must refuse (positive control) and permits what it must.
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import re
import shutil
import subprocess
import sys
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

HOME = pathlib.Path(__file__).resolve().parents[1]
AGENT_ROOT = HOME / "hermes-agent"
PINNED = (HOME / "PINNED_VERSION").read_text().split()[-1]

# The flow's modules: everything a Telegram message passes through on its way
# to the agent, plus the confirmation gate. Package A appends its mode-detection,
# dedup and draft modules here when they land.
FLOW_MODULES = [
    "gateway/run.py",
    "gateway/platforms/base.py",
    "gateway/session.py",
    "gateway/claim_gate.py",
    "tools/slash_confirm.py",
]
# The one place a vendor client may be named.
PROVIDER_LAYER = ("providers/", "plugins/model-providers/", "agent/")
VENDOR_IMPORT = re.compile(
    r"^\s*(?:import|from)\s+(anthropic|openai|google\.generativeai|google\.genai|"
    r"cohere|mistralai)\b",
    re.M,
)
MARKER = "crew182-phone-marker-5f1c"


def _need_agent() -> None:
    assert AGENT_ROOT.is_dir(), (
        f"hermes-agent is not checked out beside this repo at {AGENT_ROOT}; "
        f"CI checks out chidionyema/hermes-agent at PINNED_VERSION {PINNED}"
    )
    if str(AGENT_ROOT) not in sys.path:
        sys.path.insert(0, str(AGENT_ROOT))


def _snapshot(root: pathlib.Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in root.rglob("*"):
        if p.is_file():
            out[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


@pytest.fixture()
def estate(tmp_path, monkeypatch):
    """A hermetic hermes home beside a laptop session that must stay untouched.

    The laptop session is a transcript file under a projects directory and a
    git working tree with a commit, the two things CP1 names.
    """
    _need_agent()
    home = tmp_path / "hermes-home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    transcript = tmp_path / "laptop" / "projects" / "slug" / "session.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text('{"type":"user","text":"laptop work"}\n' * 3)
    tree = tmp_path / "laptop" / "worktree"
    tree.mkdir()
    subprocess.run(["git", "init", "-q", str(tree)], check=True)
    (tree / "app.py").write_text("print('laptop')\n")
    subprocess.run(
        ["git", "-C", str(tree), "-c", "user.email=t@t", "-c", "user.name=t",
         "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(tree), "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-q", "-m", "laptop"], check=True)
    return SimpleNamespace(root=tmp_path, home=home, transcript=transcript, tree=tree)


def _make_runner(reply: str):
    """A GatewayRunner with the agent run stubbed, the shape the upstream
    gateway tests use (tests/gateway/test_stacked_skill_platform_disabled.py)."""
    from gateway.config import GatewayConfig, Platform, PlatformConfig
    from gateway.run import GatewayRunner
    from gateway.session import SessionEntry, SessionSource, build_session_key

    source = SessionSource(platform=Platform.TELEGRAM, user_id="u1", chat_id="c1",
                           user_name="founder", chat_type="dm")
    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="***")})
    adapter = MagicMock()
    adapter.send = AsyncMock()
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._voice_mode = {}
    runner.hooks = SimpleNamespace(emit=AsyncMock(), emit_collect=AsyncMock(return_value=[]),
                                   loaded_hooks=False)
    entry = SessionEntry(session_key=build_session_key(source), session_id="sess-phone",
                         created_at=datetime.now(), updated_at=datetime.now(),
                         platform=Platform.TELEGRAM, chat_type="dm")
    runner.session_store = MagicMock()
    runner.session_store.get_or_create_session.return_value = entry
    runner.session_store.load_transcript.return_value = []
    runner.session_store.has_any_sessions.return_value = True
    runner._running_agents = {}
    runner._pending_messages = {}
    runner._pending_approvals = {}
    runner._session_db = None
    runner._reasoning_config = None
    runner._provider_routing = {}
    runner._fallback_model = None
    runner._show_reasoning = False
    runner._is_user_authorized = lambda _source: True
    runner._set_session_env = lambda _context: None
    runner._should_send_voice_reply = lambda *_a, **_k: False
    runner._session_key_for_source = GatewayRunner._session_key_for_source.__get__(
        runner, GatewayRunner)
    runner._run_agent = AsyncMock(return_value={"final_response": reply})
    return runner, source


async def _phone_message(estate, monkeypatch, text: str) -> str | None:
    from gateway import run as gateway_run
    from gateway.platforms.base import MessageEvent

    monkeypatch.setattr(gateway_run, "_resolve_runtime_agent_kwargs",
                        lambda: {"api_key": "***"})
    runner, source = _make_runner(reply=f"drafted {text}")
    return await runner._handle_message(MessageEvent(text=text, source=source,
                                                     message_id="m1"))


@pytest.mark.asyncio
async def test_incident_crew182_cp1_a_phone_message_never_reaches_the_laptop_session(
        estate, monkeypatch):
    """CP1: no message or file from the phone flow lands in the laptop session's
    transcript or working tree."""
    before = _snapshot(estate.root / "laptop")
    result = await _phone_message(estate, monkeypatch, f"build {MARKER}")
    assert result is not None and MARKER in result, "the flow did handle the message"
    assert _snapshot(estate.root / "laptop") == before, "laptop files changed"
    status = subprocess.run(["git", "-C", str(estate.tree), "status", "--porcelain"],
                            capture_output=True, text=True, check=True).stdout
    assert status == "", f"the laptop working tree is dirty:\n{status}"
    assert MARKER not in estate.transcript.read_text()
    # Positive control: the guard sees a write when one happens.
    (estate.tree / "phone.txt").write_text(MARKER)
    assert _snapshot(estate.root / "laptop") != before


@pytest.mark.asyncio
async def test_incident_crew182_cp2_handling_writes_only_under_hermes_home(
        estate, monkeypatch):
    """CP2: every file the handling touches sits under HERMES_HOME; nothing is
    shared with the laptop session."""
    before = _snapshot(estate.root)
    await _phone_message(estate, monkeypatch, f"build {MARKER}")
    after = _snapshot(estate.root)
    touched = {p for p in set(before) | set(after) if before.get(p) != after.get(p)}
    outside = sorted(p for p in touched if not p.startswith(str(estate.home)))
    assert outside == [], f"the phone flow wrote outside HERMES_HOME: {outside}"
    leaked = [p for p, h in after.items()
              if not p.startswith(str(estate.home)) and MARKER in pathlib.Path(p).read_text(
                  errors="ignore")]
    assert leaked == [], f"the message text leaked into: {leaked}"


def test_incident_crew182_cp2_live_gateway_is_its_own_process_and_shares_no_session_file():
    """CP2, live half: the gateway's PID is not this process, and the files it
    holds open are all under its own home, none under the laptop runtime's
    projects directory. HERMES_LIVE_HOME points at the running tree when the
    test runs from a worktree; SKIP is a result on a runner with no gateway."""
    live_home = pathlib.Path(os.environ.get("HERMES_LIVE_HOME", HOME))
    pid_file = live_home / "gateway.pid"
    if not pid_file.is_file() or shutil.which("lsof") is None:
        pytest.skip("no gateway.pid on this machine (runner), or no lsof")
    import json

    info = json.loads(pid_file.read_text())
    pid = int(info["pid"])
    try:
        os.kill(pid, 0)
    except OSError:
        pytest.skip(f"gateway pid {pid} is not running")
    assert pid != os.getpid()
    assert pathlib.Path(info["hermes_home"]).resolve() == live_home.resolve()
    out = subprocess.run(["lsof", "-p", str(pid)], capture_output=True, text=True).stdout
    laptop_runtime = str(pathlib.Path.home() / ".claude" / "projects")
    shared = [line for line in out.splitlines() if laptop_runtime in line]
    assert shared == [], f"the gateway holds laptop session files open:\n{shared}"


@pytest.mark.parametrize("provider", ["anthropic", "openrouter"])
@pytest.mark.asyncio
async def test_incident_crew182_cp12_confirmation_gate_is_the_same_under_any_provider(
        estate, provider):
    """CP12: swapping `model.provider` in config.yaml, with no code change, leaves
    the confirmation gate's behaviour identical: nothing runs until a choice is
    made, the choice runs the handler once, a wrong id runs nothing."""
    (estate.home / "config.yaml").write_text(
        f"model:\n  provider: {provider}\n  default: some-model\n")
    from hermes_cli import config as hermes_config
    from tools import slash_confirm

    loaded = hermes_config.load_config()
    assert loaded["model"]["provider"] == provider, "config.yaml is the one source"

    ran: list[str] = []

    async def handler(choice: str) -> str:
        ran.append(choice)
        return f"ran-{choice}"

    key = f"agent:main:telegram:dm:{provider}"
    slash_confirm.clear(key)
    slash_confirm.register(key, "c1", "/board", handler)
    assert ran == [], "the gate ran the command before a choice"
    assert slash_confirm.get_pending(key)["confirm_id"] == "c1"
    assert await slash_confirm.resolve(key, "wrong-id", "once") is None
    assert ran == [], "a wrong id must run nothing"
    assert await slash_confirm.resolve(key, "c1", "once") == "ran-once"
    assert ran == ["once"]
    assert slash_confirm.get_pending(key) is None, "a resolved confirm is gone"


def test_incident_crew182_cp13_no_flow_module_imports_a_model_vendor():
    """CP13, source half: the flow's modules name no vendor SDK; only the
    provider layer does. Both ways: the scanner must see the provider layer's
    own imports, or it is blind."""
    _need_agent()
    hits = {m: VENDOR_IMPORT.findall((AGENT_ROOT / m).read_text()) for m in FLOW_MODULES}
    assert all((AGENT_ROOT / m).is_file() for m in FLOW_MODULES), hits
    assert {m: h for m, h in hits.items() if h} == {}, hits
    seen = [str(p.relative_to(AGENT_ROOT)) for p in AGENT_ROOT.rglob("*.py")
            if any(str(p.relative_to(AGENT_ROOT)).startswith(d) for d in PROVIDER_LAYER)
            and VENDOR_IMPORT.search(p.read_text(errors="ignore"))]
    assert seen, "the scanner found no vendor import in the provider layer: it is blind"
    assert "agent/anthropic_adapter.py" in seen
