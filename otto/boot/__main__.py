"""``python -m otto.boot`` — the deploy contract.

Two modes:

* ``python -m otto.boot`` — start the webhook server. Binds
  ``0.0.0.0:$OTTO_BOOT_PORT`` (default 8080) and serves until killed.
* ``python -m otto.boot --set-webhook <url>`` — call Telegram's
  ``setWebhook`` with that URL and exit; does not start a server.

Both modes read ``OTTO_TELEGRAM_BOT_TOKEN`` and refuse loudly, before
doing anything else, when it is absent (``BootRefused`` printed to
stderr as structured JSON, exit code 2) — LAW 46 and LAW 50 together: no
literal token, and no silent idle process pretending to be up.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable

from otto.boot.config import BootConfig, read_api_base, read_token
from otto.boot.errors import BootRefused
from otto.boot.pipeline import boot_obs_handles, build_registry
from otto.boot.server import ServerDeps, build_server
from otto.boot.transport import TelegramHTTPTransport, TelegramTransport
from otto.gateway.core import ToolGateway
from otto.surface.bindings.telegram import TelegramBinding

TransportFactory = Callable[[str, str], TelegramTransport]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m otto.boot")
    parser.add_argument(
        "--set-webhook",
        metavar="URL",
        default=None,
        help="register URL as the Telegram webhook and exit (no server starts)",
    )
    return parser


def _run_set_webhook(
    url: str, *, transport_factory: TransportFactory, environ: dict[str, str] | None
) -> int:
    try:
        token = read_token(environ)
    except BootRefused as exc:
        print(_refusal_line(exc), file=sys.stderr)
        return 2
    transport = transport_factory(token, read_api_base(environ))
    transport.set_webhook(url)
    print(f"otto.boot: webhook registered ({WEBHOOK_ROUTE_NOTE})")
    return 0


WEBHOOK_ROUTE_NOTE = "the URL must resolve to this deployment's /telegram-webhook route"


def _run_server(
    *, transport_factory: TransportFactory, environ: dict[str, str] | None
) -> int:
    try:
        config = BootConfig.from_env(environ)
    except BootRefused as exc:
        print(_refusal_line(exc), file=sys.stderr)
        return 2

    obs = boot_obs_handles()  # LAW 50: refuses to run dark, own error path
    binding = TelegramBinding(chat_id_allowlist=config.chat_allowlist)
    gateway = ToolGateway(registry=build_registry())
    transport = transport_factory(config.token, config.telegram_api_base)
    deps = ServerDeps(binding=binding, gateway=gateway, obs=obs, transport=transport)

    server = build_server(deps, config.port)
    print(
        f"otto.boot: listening on 0.0.0.0:{config.port}, "
        f"{len(config.chat_allowlist)} chat id(s) allowlisted"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
    return 0


def _refusal_line(exc: BootRefused) -> str:
    import json

    return json.dumps(exc.as_dict())


def main(
    argv: list[str] | None = None,
    *,
    transport_factory: TransportFactory = TelegramHTTPTransport,
    environ: dict[str, str] | None = None,
) -> int:
    args = _build_parser().parse_args(argv)
    if args.set_webhook:
        return _run_set_webhook(
            args.set_webhook, transport_factory=transport_factory, environ=environ
        )
    return _run_server(transport_factory=transport_factory, environ=environ)


if __name__ == "__main__":
    sys.exit(main())
