"""Provider client protocol and failure classes.

The router never talks HTTP itself; it calls a ``ProviderClient`` and maps
its three failure classes to policy: timeout (budget-charged retry then
pause), 5xx (bounded retry then needs_human), egress denied (fail closed at
once — a network policy cannot be waited out). There is no fallback-provider
path anywhere in this module or its callers: an unconfigured provider is
never a repair.

``LiteLLMClient`` is the one concrete client, mirroring the estate's
``bin/consult`` pattern: base URL and key come from the environment or the
named secrets file; the key value is never printed, logged or embedded.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

_KEY_FILE_RELATIVE = ".config/prospector/secrets.d/LITELLM_API_KEY"
_DEFAULT_BASE_URL = "https://llm.mumchimp.com/v1"

#: Completion budget for one call, and it has to cover the model's own
#: thinking as well as its answer. Measured against the estate router on
#: 2026-09-04: `moonshot/kimi-k3` spent 1,030 reasoning tokens to produce
#: the three words "kimi is live", and the same lane asked with a 200
#: token cap returned an empty string -- the whole budget went on thought
#: and nothing was left for output. This lane's prompt then asks for a
#: JSON object, so a truncated completion is not merely short, it is
#: unparseable and arrives at the founder as "the model replied in a
#: shape I refuse to parse". 8192 leaves room for both halves on a
#: reasoning model; a lane that does not think spends only what it needs,
#: because this is a cap and not an allocation.
_DEFAULT_MAX_TOKENS = 8192


def _max_tokens() -> int:
    raw = os.environ.get("OTTO_ROUTER_MAX_TOKENS")
    if not raw:
        return _DEFAULT_MAX_TOKENS
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_MAX_TOKENS
    return value if value > 0 else _DEFAULT_MAX_TOKENS


class ProviderTimeout(Exception):
    """The provider did not answer inside the configured timeout."""


class ProviderHTTPError(Exception):
    """The provider answered with an HTTP error status."""

    def __init__(self, status: int, message: str = "") -> None:
        super().__init__(f"HTTP {status} {message}".strip())
        self.status = status


class EgressDenied(Exception):
    """The network refused the connection (egress policy, DNS block, no route)."""


@dataclass(frozen=True)
class ProviderResult:
    """Raw provider output plus usage accounting."""

    text: str
    tokens: int


class ProviderClient(Protocol):
    """One call to one model. Raises the three failure classes above."""

    def complete(
        self, model: str, payload: str, timeout_seconds: float
    ) -> ProviderResult: ...


def _read_key() -> str:
    """The key VALUE never leaves this function except in the auth header."""
    k = os.environ.get("LITELLM_API_KEY", "").strip()
    if k:
        return k
    try:
        return (Path.home() / _KEY_FILE_RELATIVE).read_text().strip()
    except OSError:
        return ""


def litellm_base_url() -> str:
    return os.environ.get("LITELLM_BASE_URL", _DEFAULT_BASE_URL).rstrip("/")


def litellm_reachable(timeout_seconds: float = 15.0) -> bool:
    """Liveness probe mirroring ``bin/consult --health`` (exit-3 philosophy:
    unreachable is normal, not an error)."""
    if not _read_key():
        return False
    base = litellm_base_url().rsplit("/v1", 1)[0]
    req = urllib.request.Request(  # noqa: S310 - https only, estate router
        f"{base}/health/liveliness",
        headers={"Authorization": f"Bearer {_read_key()}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds):  # noqa: S310
            return True
    except Exception:  # noqa: BLE001 - any failure means "take the fallback"
        return False


@dataclass(frozen=True)
class LiteLLMClient:
    """Concrete client for the estate model router (LiteLLM API shape)."""

    max_tokens: int = field(default_factory=_max_tokens)

    def complete(
        self, model: str, payload: str, timeout_seconds: float
    ) -> ProviderResult:
        key = _read_key()
        if not key:
            raise EgressDenied("no LITELLM_API_KEY in environment or secrets.d")
        body = json.dumps(
            {
                "model": model,
                "max_tokens": self.max_tokens,
                "messages": [{"role": "user", "content": payload}],
            }
        ).encode()
        req = urllib.request.Request(  # noqa: S310 - https only, estate router
            f"{litellm_base_url()}/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_seconds) as r:  # noqa: S310
                obj = json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as exc:
            raise ProviderHTTPError(exc.code) from exc
        except TimeoutError as exc:
            raise ProviderTimeout(str(exc)) from exc
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, TimeoutError):
                raise ProviderTimeout(str(reason)) from exc
            raise EgressDenied(str(reason)) from exc
        text = ((obj.get("choices") or [{}])[0].get("message") or {}).get(
            "content"
        ) or ""
        usage = obj.get("usage") or {}
        tokens = int(usage.get("total_tokens") or 0)
        return ProviderResult(text=text, tokens=tokens)
