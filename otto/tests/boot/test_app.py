"""``otto.boot.app.handle_webhook_body`` — the four required cases,
bytes in, ``WebhookResult`` out, never a raised exception. No test in
this module opens a socket: ``handle_webhook_body`` is a pure function
over bytes, exercised directly.
"""

from __future__ import annotations

import pytest

from otto.boot.app import (
    BAD_REQUEST_RESPONSE,
    DELIVERED_RESPONSE,
    DROPPED_RESPONSE,
    handle_webhook_body,
)
from otto.boot.pipeline import boot_obs_handles, build_registry
from otto.gateway.core import ToolGateway
from otto.surface.bindings.telegram import TelegramBinding
from otto.tests.boot.fakes import FakeTransport

_ALLOWLIST = {111: "founder"}


@pytest.fixture()
def deps():
    obs = boot_obs_handles()
    try:
        yield {
            "binding": TelegramBinding(chat_id_allowlist=_ALLOWLIST),
            "gateway": ToolGateway(registry=build_registry()),
            "obs": obs,
            "transport": FakeTransport(),
        }
    finally:
        for handle in (obs.boot, obs.spine, obs.gateway, obs.router, obs.memory):
            handle.shutdown()


def test_allowlisted_message_is_delivered(deps) -> None:
    body = b'{"message": {"chat": {"id": 111}, "text": "hi", "date": 1700000000}}'
    result = handle_webhook_body(body, **deps)
    assert result.status == 200
    assert result.body == DELIVERED_RESPONSE
    assert deps["transport"].sent  # exactly one sendMessage happened
    assert deps["transport"].sent[0][0] == 111


def test_unrecognised_chat_id_is_dropped_not_crashed(deps) -> None:
    body = b'{"message": {"chat": {"id": 999}, "text": "hi", "date": 1700000000}}'
    result = handle_webhook_body(body, **deps)
    assert result.status == 200
    assert result.body == DROPPED_RESPONSE
    assert deps["transport"].sent == []


@pytest.mark.parametrize(
    "raw_body",
    [
        b"not json at all",
        b"",
        b"[1, 2, 3]",
        b'"just a string"',
        b'{"message": "not-a-dict"}',
        b'{"message": {"chat": "not-a-dict", "text": "hi"}}',
    ],
)
def test_malformed_payloads_get_a_400_and_never_crash(deps, raw_body) -> None:
    result = handle_webhook_body(raw_body, **deps)
    assert result.status == 400
    assert result.body == BAD_REQUEST_RESPONSE
    assert deps["transport"].sent == []


def test_telegram_shaped_update_with_no_message_is_dropped_not_crashed(
    deps,
) -> None:
    """A callback query (or any update this lane has nothing to do with)
    is still telegram-shaped; it is acknowledged and dropped, not
    rejected — Telegram's own guidance is to ack fast, never 4xx an
    update shape it simply does not use."""
    body = b'{"callback_query": {"id": "1", "data": "x"}}'
    result = handle_webhook_body(body, **deps)
    assert result.status == 200
    assert result.body == DROPPED_RESPONSE


def test_a_pipeline_exception_is_dropped_not_crashed(deps, monkeypatch) -> None:
    """The one place an unexpected exception from any lane must never
    reach the socket layer as a crash: it becomes a 200-and-drop, logged
    through the boot obs handle, never raised."""
    import otto.boot.app as app_module

    def _boom(*args, **kwargs):
        raise RuntimeError("a lane blew up")

    monkeypatch.setattr(app_module, "process_update", _boom)
    body = b'{"message": {"chat": {"id": 111}, "text": "hi", "date": 1700000000}}'
    result = handle_webhook_body(body, **deps)
    assert result.status == 200
    assert result.body == DROPPED_RESPONSE
    assert deps["transport"].sent == []
