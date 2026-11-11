"""A real disposable PostgreSQL transaction protects founder widget delivery.

This suite runs under bin/otto-demo on GitHub-hosted Ubuntu with an actual
PostgreSQL 16 process. Only the external Telegram network is intercepted.
"""
from types import SimpleNamespace

import psycopg

from otto.ingress.office_delivery import auto_publish_widget


def test_real_postgres_idempotent_founder_send_across_process_restart(
    postgres_handle, monkeypatch
):
    import otto.ingress.office_delivery as delivery

    actual_sends = []
    def recorded_send(self, chat_id, widget_id, chat_data):
        actual_sends.append((chat_id, widget_id, chat_data))
        return 2418

    monkeypatch.setattr(delivery.TelegramHTTPTransport, "send_widget", recorded_send)
    chat = "748392"
    token = "unit-test-telegram-bot"
    binding = SimpleNamespace(
        tenant_id="estate", external_id=chat,
        principal_allowlist={chat: "founder"},
        outbound_secret_ref="vault://test/bot",
    )
    gateway = SimpleNamespace(
        _store=SimpleNamespace(find_by_credential=lambda channel, presented: binding),
        _secrets=SimpleNamespace(resolve=lambda ref: token),
        _safe_brief=lambda: {"r": 0, "at": 0, "delivered": None},
    )
    env = {
        "OTTO_OPERATOR_CHAT_ID": chat,
        "OTTO_TELEGRAM_BOT_TOKEN": token,
        "OTTO_CHANNEL_SECRET_VAULT_OTTO_STAGING_TELEGRAM_WEBHOOK_SECRET": "test-webhook",
    }
    first = auto_publish_widget(gateway, dsn=postgres_handle.dsn, environ=env)
    second = auto_publish_widget(gateway, dsn=postgres_handle.dsn, environ=env)
    assert first == {"status": "DELIVERED", "message_id": 2418}
    assert second == {"status": "ALREADY_DELIVERED", "message_id": 2418}
    assert actual_sends == [(748392, "otto_founder_compass", {"r": 0, "at": 0, "delivered": None})]
    with psycopg.connect(postgres_handle.dsn) as conn:
        row = conn.execute(
            "SELECT state, message_id FROM otto_founder_widget_delivery"
        ).fetchone()
        assert row == ("DELIVERED", 2418)


def test_real_postgres_failed_telegram_send_is_never_marked_delivered(
    postgres_handle, monkeypatch
):
    import otto.ingress.office_delivery as delivery

    def rejected(*args, **kwargs):
        raise RuntimeError("Bot API refused")
    monkeypatch.setattr(delivery.TelegramHTTPTransport, "send_widget", rejected)
    chat = "748393"
    token = "different-test-telegram-bot"
    binding = SimpleNamespace(
        tenant_id="estate", external_id=chat,
        principal_allowlist={chat: "founder"},
        outbound_secret_ref="vault://test/bot",
    )
    gateway = SimpleNamespace(
        _store=SimpleNamespace(find_by_credential=lambda *_: binding),
        _secrets=SimpleNamespace(resolve=lambda *_: token),
        _safe_brief=lambda: {"r": 0, "at": 0},
    )
    env = {
        "OTTO_OPERATOR_CHAT_ID": chat,
        "OTTO_TELEGRAM_BOT_TOKEN": token,
        "OTTO_CHANNEL_SECRET_VAULT_OTTO_STAGING_TELEGRAM_WEBHOOK_SECRET": "webhook",
    }
    result = auto_publish_widget(gateway, dsn=postgres_handle.dsn, environ=env)
    assert result["status"] == "FAILED"
    with psycopg.connect(postgres_handle.dsn) as conn:
        assert conn.execute(
            "SELECT state, message_id FROM otto_founder_widget_delivery WHERE state='FAILED'"
        ).fetchone() == ("FAILED", None)
