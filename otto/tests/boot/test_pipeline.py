"""``otto.boot.pipeline`` crossing the platform lanes for a real update.

Same shape as ``otto/tests/integration/test_smoke_assembly.py``'s
end-to-end walk, run against ``process_update``/``deliver`` instead of
by hand: an allowlisted chat id gets a reply through a fake transport;
an unrecognised chat id is capped to T1 by the taint rule and the
gateway denies its T2 tool call, so no reply is ever built and nothing
is ever sent.
"""

from __future__ import annotations

from types import SimpleNamespace

from otto.boot.pipeline import (
    _build_tool_loop,
    boot_obs_handles,
    build_registry,
    deliver,
    process_update,
)
from otto.gateway.bridge import ForkToolDenied
from otto.gateway.core import ToolGateway
from otto.gateway.registry import Tier
from otto.surface.bindings.telegram import TelegramBinding
from otto.tests.boot.fakes import FakeProviderClient, FakeTransport

_ALLOWLIST = {111: "founder"}


def _obs():
    handles = boot_obs_handles()
    return handles


def test_allowlisted_chat_round_trip_produces_a_send_message() -> None:
    obs = _obs()
    try:
        binding = TelegramBinding(chat_id_allowlist=_ALLOWLIST)
        gateway = ToolGateway(registry=build_registry())
        transport = FakeTransport()

        native_event = {
            "message": {
                "chat": {"id": 111},
                "text": "hello otto",
                "date": 1_700_000_000,
            }
        }
        outcome = process_update(
            native_event,
            binding=binding,
            registry_gateway=gateway,
            obs=obs,
            provider_client=FakeProviderClient(answer="Otto here, and this is an answer."),
        )
        assert outcome.gateway_response is not None
        assert not outcome.gateway_response.denied
        assert outcome.reply_chat_id == 111
        assert outcome.reply_text is not None
        # The reply is the model's answer, not the sender's own words echoed
        # back. The lane used to reply "noted: <your text>" to everything,
        # which is what the founder reported three times as "not responding".
        assert "Otto here, and this is an answer." in outcome.reply_text
        assert "hello otto" not in outcome.reply_text
        # P1: the router alone never verifies -- every reply this lane ever
        # renders carries the unverified marker.
        assert outcome.reply_text.startswith("⚠")

        delivered = deliver(outcome, transport)
        assert delivered is True
        assert transport.sent == [(111, outcome.reply_text)]
    finally:
        for handle in (obs.boot, obs.spine, obs.gateway, obs.router, obs.memory):
            handle.shutdown()


def test_unrecognised_chat_id_gets_no_tool_authority_and_no_reply() -> None:
    obs = _obs()
    try:
        binding = TelegramBinding(chat_id_allowlist=_ALLOWLIST)
        gateway = ToolGateway(registry=build_registry())
        transport = FakeTransport()

        native_event = {
            "message": {
                "chat": {"id": 999},
                "text": "hello otto",
                "date": 1_700_000_000,
            }
        }
        outcome = process_update(
            native_event, binding=binding, registry_gateway=gateway, obs=obs
        )
        # The sender is untrusted; its task envelope still crosses the
        # gateway lane (P5 holds under a real message, not just in a unit
        # test on the envelope alone) but is capped to T1 while the one
        # registered tool sits at T2, so the gateway denies the call.
        assert outcome.task_envelope is not None
        assert outcome.task_envelope.is_taint_capped
        assert outcome.gateway_response is not None
        assert outcome.gateway_response.denied
        assert outcome.router_response is None
        assert outcome.fact is None
        assert outcome.reply_chat_id is None
        assert outcome.reply_text is None

        delivered = deliver(outcome, transport)
        assert delivered is False
        assert transport.sent == []
    finally:
        for handle in (obs.boot, obs.spine, obs.gateway, obs.router, obs.memory):
            handle.shutdown()


def test_empty_text_produces_no_reply_and_does_not_cross_the_gateway() -> None:
    """A non-instruction-bearing update (blank text) stops at the surface
    step; the gateway, router and memory lanes never run for it."""
    obs = _obs()
    try:
        binding = TelegramBinding(chat_id_allowlist=_ALLOWLIST)
        gateway = ToolGateway(registry=build_registry())

        native_event = {
            "message": {"chat": {"id": 111}, "text": "", "date": 1_700_000_000}
        }
        outcome = process_update(
            native_event, binding=binding, registry_gateway=gateway, obs=obs
        )
        assert outcome.task_envelope is None
        assert outcome.gateway_response is None
        assert outcome.reply_text is None
    finally:
        for handle in (obs.boot, obs.spine, obs.gateway, obs.router, obs.memory):
            handle.shutdown()


class _RecordingRouter:
    def __init__(self) -> None:
        self.lines: list[tuple[str, dict]] = []

    def info(self, event: str, ctx, **fields) -> None:
        self.lines.append((event, fields))


class _RecordingObs:
    def __init__(self) -> None:
        self.router = _RecordingRouter()


class _RefusingGateway(ToolGateway):
    """The registry's terminal handler, as ``otto.gateway.bridge`` refuses at T2."""

    def call(self, env, name, args):
        raise ForkToolDenied(
            f"command {args['command']!r} matched the un-undoable set; route it to "
            "terminal_irreversible (T3) for the human gate."
        )


def test_a_refused_irreversible_command_is_a_denied_turn_the_model_reads() -> None:
    """The T2 terminal guard refuses ``rm -rf`` before it runs (``otto.gateway.bridge``).
    That refusal used to escape the tool loop as an exception; the ingress worker
    nak'd the task, JetStream redelivered it, and the founder's 21:54Z message on
    2026-09-06 restarted 31 times before it was answered. The refusal is a denied
    turn: text the model reads, one ``router.tool_turn`` line, no exception."""
    obs = _RecordingObs()
    _tools, execute = _build_tool_loop(
        ceiling=Tier.T2,
        registry_gateway=_RefusingGateway(registry=build_registry()),
        obs=obs,
        ctx=SimpleNamespace(task_ulid="01TESTDENIEDTURN"),
    )

    result = execute("terminal", '{"command": "rm -rf /tmp/otto-scratch"}')

    assert result.startswith("denied: ")
    assert "rm -rf" in result
    turns = [f for e, f in obs.router.lines if e == "router.tool_turn"]
    assert len(turns) == 1
    assert turns[0]["denied"] is True
    assert turns[0]["reason"] == "irreversible_command"
