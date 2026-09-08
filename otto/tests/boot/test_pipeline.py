"""``otto.boot.pipeline`` crossing the platform lanes for a real update.

Same shape as ``otto/tests/integration/test_smoke_assembly.py``'s
end-to-end walk, run against ``process_update``/``deliver`` instead of
by hand: an allowlisted chat id gets a reply through a fake transport;
an unrecognised chat id is capped to T1 by the taint rule and the
gateway denies its T2 tool call, so no reply is ever built and nothing
is ever sent.
"""

from __future__ import annotations

from otto.boot.pipeline import boot_obs_handles, build_registry, deliver, process_update
from otto.gateway.core import ToolGateway
from otto.surface.bindings.telegram import TelegramBinding
from otto.tests.boot.fakes import FakeTransport

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
            native_event, binding=binding, registry_gateway=gateway, obs=obs
        )
        assert outcome.gateway_response is not None
        assert not outcome.gateway_response.denied
        assert outcome.reply_chat_id == 111
        assert outcome.reply_text is not None
        assert "noted: hello otto" in outcome.reply_text
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
