"""``python -m otto.boot`` — the deploy contract's entrypoint.

No test here ever calls a real ``serve_forever()`` (it would block
forever) and no test ever calls a real ``TelegramHTTPTransport`` (it
would dial out). The one exception path proved fully is also the one
LAW 46/LAW 50 care about most: a missing token refuses loudly, before
anything else boots, rather than starting an idle process that only
looks alive.
"""

from __future__ import annotations

import json

from otto.boot.__main__ import main
from otto.boot.config import CONFIG_PATH_ENV, TOKEN_ENV
from otto.tests.boot.fakes import FakeTransport


def _never_called(*args, **kwargs):
    raise AssertionError(
        "a transport must never be constructed when the token is missing"
    )


def test_missing_token_refuses_before_starting_a_server(capsys) -> None:
    exit_code = main([], transport_factory=_never_called, environ={})
    assert exit_code == 2
    err = capsys.readouterr().err
    refusal = json.loads(err)
    assert refusal["error"] == "otto.boot.refused"
    assert TOKEN_ENV in refusal["reason"]


def test_missing_token_refuses_before_set_webhook(capsys) -> None:
    exit_code = main(
        ["--set-webhook", "https://example.test/telegram-webhook"],
        transport_factory=_never_called,
        environ={},
    )
    assert exit_code == 2
    err = capsys.readouterr().err
    refusal = json.loads(err)
    assert refusal["error"] == "otto.boot.refused"
    assert TOKEN_ENV in refusal["reason"]


def test_set_webhook_registers_with_the_transport_and_exits_zero(capsys) -> None:
    fake = FakeTransport()

    def _factory(token: str, api_base: str) -> FakeTransport:
        assert token == "a-token-value"  # noqa: S105 - synthetic test placeholder
        return fake

    exit_code = main(
        ["--set-webhook", "https://example.test/telegram-webhook"],
        transport_factory=_factory,
        environ={TOKEN_ENV: "a-token-value"},
    )
    assert exit_code == 0
    assert fake.webhooks_set == ["https://example.test/telegram-webhook"]
    # No server route is ever exercised in --set-webhook mode.
    assert fake.sent == []


def test_server_mode_assembles_config_and_serves_without_blocking(
    monkeypatch, tmp_path, capsys
) -> None:
    """Proves the full assembly (config, obs, binding, gateway, transport,
    server) happens on the success path, without ever calling a real
    blocking ``serve_forever()`` or a real Telegram HTTP call."""
    import otto.boot.__main__ as main_module

    config_file = tmp_path / "boot.yaml"
    config_file.write_text("chat_allowlist:\n  111: founder\n")

    built = {}

    class _FakeServer:
        def serve_forever(self) -> None:
            built["served"] = True

    def _fake_build_server(deps, port, bind="0.0.0.0"):  # noqa: S104 - a fake, never binds a real socket
        built["deps"] = deps
        built["port"] = port
        return _FakeServer()

    monkeypatch.setattr(main_module, "build_server", _fake_build_server)

    fake_transport = FakeTransport()
    exit_code = main(
        [],
        transport_factory=lambda token, api_base: fake_transport,
        environ={
            TOKEN_ENV: "a-token-value",
            CONFIG_PATH_ENV: str(config_file),
        },
    )
    assert exit_code == 0
    assert built["served"] is True
    assert built["port"] == 8080
    assert built["deps"].binding.chat_id_allowlist == {111: "founder"}
    assert built["deps"].transport is fake_transport
    out = capsys.readouterr().out
    assert "listening" in out
    # The token value itself is never printed.
    assert "a-token-value" not in out
