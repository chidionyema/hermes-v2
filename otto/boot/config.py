"""Boot-lane configuration — every value named by an environment variable
(LAW 46: no checkout, host, port, account or credential lives here as a
literal).

* ``OTTO_TELEGRAM_BOT_TOKEN`` — the bot token. Read once, held only in
  memory, never logged, never written to a file. Required; its absence
  is a loud refusal, not an idle process.
* ``OTTO_BOOT_CONFIG`` — path to a YAML file naming the chat-id
  allowlist this deployment trusts as an operator (the same allowlist
  shape ``otto.surface.bindings.telegram.TelegramBinding`` already
  takes: ``{chat_id: principal_name}``). Required; a boot lane with no
  named allowlist trusts nobody and cannot even discover that, which is
  worse than refusing to start.
* ``OTTO_BOOT_PORT`` — the port the HTTP server binds. Optional,
  defaults to 8080.
* ``OTTO_BOOT_TELEGRAM_API_BASE`` — the Telegram Bot API base URL.
  Optional, defaults to the real API; the test suite overrides it to
  prove nothing here is hardcoded, even though nothing in the test
  suite ever dials out.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import yaml

from otto.boot.errors import BootRefused

TOKEN_ENV = "OTTO_TELEGRAM_BOT_TOKEN"  # noqa: S105 - an env var name, not a token value
CONFIG_PATH_ENV = "OTTO_BOOT_CONFIG"
PORT_ENV = "OTTO_BOOT_PORT"
API_BASE_ENV = "OTTO_BOOT_TELEGRAM_API_BASE"

_DEFAULT_PORT = 8080
_DEFAULT_API_BASE = "https://api.telegram.org"


def _refuse(reason: str, remedy: str) -> BootRefused:
    return BootRefused(reason, remedy)


def read_token(environ: dict[str, str] | None = None) -> str:
    """The token value, read exactly once. Raises ``BootRefused`` when
    absent — the caller never receives an empty string standing in for
    "no token", because an empty string is truthy-adjacent enough to
    slip past a lazy check somewhere downstream."""
    env = os.environ if environ is None else environ
    token = (env.get(TOKEN_ENV) or "").strip()
    if not token:
        raise _refuse(
            f"{TOKEN_ENV} is not set; the boot lane will not run dark",
            f"set {TOKEN_ENV} in the deployment's environment (never a literal in a file)",
        )
    return token


def _load_allowlist(path: str) -> dict[int, str]:
    try:
        with open(path, encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
    except OSError as exc:
        raise _refuse(
            f"{CONFIG_PATH_ENV} names {path!r}, which could not be read: {exc}",
            f"point {CONFIG_PATH_ENV} at a readable YAML file",
        ) from exc
    except yaml.YAMLError as exc:
        raise _refuse(
            f"{CONFIG_PATH_ENV} names {path!r}, which is not valid YAML: {exc}",
            "fix the YAML syntax in the boot config file",
        ) from exc
    if not isinstance(raw, dict):
        raise _refuse(
            f"{path!r} must parse to a mapping with a chat_allowlist key",
            "wrap the file's content in a mapping, e.g. 'chat_allowlist: {...}'",
        )
    allowlist = raw.get("chat_allowlist") or {}
    if not isinstance(allowlist, dict):
        raise _refuse(
            f"{path!r}'s chat_allowlist must be a mapping of chat id to principal name",
            "e.g. 'chat_allowlist: {123456789: founder}'",
        )
    parsed: dict[int, str] = {}
    for chat_id, principal in allowlist.items():
        try:
            key = int(chat_id)
        except (TypeError, ValueError) as exc:
            raise _refuse(
                f"{path!r}'s chat_allowlist key {chat_id!r} is not an integer chat id",
                "every chat_allowlist key must be the numeric Telegram chat id",
            ) from exc
        if not isinstance(principal, str) or not principal.strip():
            raise _refuse(
                f"{path!r}'s chat_allowlist entry for {chat_id!r} has no principal name",
                "every chat_allowlist value must be a non-empty principal name string",
            )
        parsed[key] = principal
    return parsed


def read_chat_allowlist(environ: dict[str, str] | None = None) -> dict[int, str]:
    env = os.environ if environ is None else environ
    path = (env.get(CONFIG_PATH_ENV) or "").strip()
    if not path:
        raise _refuse(
            f"{CONFIG_PATH_ENV} is not set; the boot lane trusts nobody and cannot say so",
            f"set {CONFIG_PATH_ENV} to a YAML file naming the operator chat-id allowlist",
        )
    return _load_allowlist(path)


def read_port(environ: dict[str, str] | None = None) -> int:
    env = os.environ if environ is None else environ
    raw = (env.get(PORT_ENV) or "").strip()
    if not raw:
        return _DEFAULT_PORT
    try:
        port = int(raw)
    except ValueError as exc:
        raise _refuse(
            f"{PORT_ENV}={raw!r} is not an integer",
            f"set {PORT_ENV} to a plain integer port, or unset it for the default {_DEFAULT_PORT}",
        ) from exc
    if not (0 < port < 65536):
        raise _refuse(
            f"{PORT_ENV}={port} is out of range",
            f"set {PORT_ENV} to a port between 1 and 65535, or unset it for {_DEFAULT_PORT}",
        )
    return port


def read_api_base(environ: dict[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    return (env.get(API_BASE_ENV) or _DEFAULT_API_BASE).rstrip("/")


@dataclass(frozen=True)
class BootConfig:
    """Everything the boot lane needs, resolved once at startup."""

    token: str
    chat_allowlist: dict[int, str]
    port: int
    telegram_api_base: str

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> BootConfig:
        return cls(
            token=read_token(environ),
            chat_allowlist=read_chat_allowlist(environ),
            port=read_port(environ),
            telegram_api_base=read_api_base(environ),
        )
