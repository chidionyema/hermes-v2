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
from typing import Callable, Protocol

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
_DEFAULT_TOOL_MAX_TURNS = 12


def _tool_max_turns() -> int:
    """Bounded tool loop budget (default 12). An env real-config override so
    a deployment can tighten it without a code change; never zero."""
    raw = os.environ.get("OTTO_ROUTER_TOOL_MAX_TURNS")
    if not raw:
        return _DEFAULT_TOOL_MAX_TURNS
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_TOOL_MAX_TURNS
    return value if value > 0 else _DEFAULT_TOOL_MAX_TURNS


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
    """One call to one model. Raises the three failure classes above.

    ``tools``/``tool_executor`` are the tool-call loop's two inputs and
    default to None so every existing caller and fake is unchanged: a client
    given no tools does a single plain completion, exactly as before.
    """

    def complete(
        self,
        model: str,
        payload: str,
        timeout_seconds: float,
        *,
        tools: list[dict] | None = None,
        tool_executor: Callable[[str, str], str] | None = None,
        history: list[dict] | None = None,
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
        self,
        model: str,
        payload: str,
        timeout_seconds: float,
        *,
        tools: list[dict] | None = None,
        tool_executor: Callable[[str, str], str] | None = None,
        history: list[dict] | None = None,
    ) -> ProviderResult:
        """One logical completion, possibly several HTTP turns (tool loop).

        When ``tools`` and ``tool_executor`` are given, the call starts with
        the user payload and the tool schema, then keeps POSTing while the
        reply carries ``tool_calls``: the assistant message is appended, each
        tool call runs through ``tool_executor(name, argstring)`` and its
        result is appended as a ``role=tool`` message, then the client asks
        again. The loop stops at ``OTTO_ROUTER_TOOL_MAX_TURNS`` (12) or at
        the first text-only reply. The per-turn observability line
        (``router.tool_turn``) is emitted by the caller-supplied executor
        wrapper in ``answer_envelope``, which is the one place an ``ObsHandle``
        actually reaches a model call.
        """
        key = _read_key()
        if not key:
            raise EgressDenied("no LITELLM_API_KEY in environment or secrets.d")
        if tools and tool_executor is None:
            raise ValueError("tool_executor is required when tools are given")

        # The conversation, then this message. Before 2026-09-10 this list
        # was the current message alone, which is why Otto could not answer
        # "summarise the URL I just sent you" -- it had never been shown the
        # turn that carried the URL. ``history`` is already in wire shape and
        # already budgeted by otto.ingress.thread.thread_messages; an
        # empty or absent history sends exactly the one message it always did.
        messages: list[dict] = [*(history or []), {"role": "user", "content": payload}]
        accumulated_tokens = 0
        turns = 0
        limit = _tool_max_turns()

        while True:
            obj = self._round_trip(
                key, model, messages, timeout_seconds, tools=tools or None
            )
            usage = obj.get("usage") or {}
            accumulated_tokens += int(usage.get("total_tokens") or 0)
            message = (obj.get("choices") or [{}])[0].get("message") or {}
            tool_calls = message.get("tool_calls") or []
            text = message.get("content") or ""

            if not tools or not tool_executor or not tool_calls:
                # Plain completion (no tool path) or the model stopped
                # calling tools: the text is the answer.
                return ProviderResult(text=text, tokens=accumulated_tokens)

            # One tool round: append the assistant's tool_calls, run each,
            # then loop for the next model turn.
            assistant_msg = dict(message)
            assistant_msg.setdefault("role", "assistant")
            messages.append(assistant_msg)
            for call in tool_calls:
                function = call.get("function") or {}
                name = function.get("name") or ""
                arguments = function.get("arguments") or "{}"
                result = tool_executor(name, arguments)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id") or "",
                        "content": result,
                    }
                )
            turns += 1
            if turns >= limit:
                # A runaway tool loop is refused, not fed forever: hand back
                # the last text under the cap rather than looping forever.
                return ProviderResult(text=text, tokens=accumulated_tokens)

    def _round_trip(
        self,
        key: str,
        model: str,
        messages: list[dict],
        timeout_seconds: float,
        *,
        tools: list[dict] | None = None,
    ) -> dict:
        """One HTTP POST to the chat completions endpoint; returns parsed JSON."""
        body: dict = {
            "model": model,
            "max_tokens": self.max_tokens,
            "messages": messages,
        }
        if tools is not None:
            body["tools"] = tools
            # Let the endpoint decide tool vs plain answer per turn. The
            # forced default is fine: the model answers directly when it
            # needs no tool, and calls one when it does.
            body["tool_choice"] = "auto"
        req = urllib.request.Request(  # noqa: S310 - https only, estate router
            f"{litellm_base_url()}/chat/completions",
            data=json.dumps(body).encode(),
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_seconds) as r:  # noqa: S310
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as exc:
            raise ProviderHTTPError(exc.code) from exc
        except TimeoutError as exc:
            raise ProviderTimeout(str(exc)) from exc
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, TimeoutError):
                raise ProviderTimeout(str(reason)) from exc
            raise EgressDenied(str(reason)) from exc
