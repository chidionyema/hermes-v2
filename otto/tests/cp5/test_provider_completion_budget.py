"""A reasoning lane needs a completion budget that covers its thinking.

Measured against the estate router on 2026-09-04: `moonshot/kimi-k3` answered
"kimi is live" using 1,049 completion tokens, 1,030 of them reasoning. The same
lane asked with a 200-token cap returned an empty string -- every token went on
thought and none on output. This boot lane's prompt asks for a JSON object, so a
truncated completion reaches the founder as "the model replied in a shape I
refuse to parse", which is the failure these cases exist to prevent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from otto.router.providers import _DEFAULT_MAX_TOKENS, LiteLLMClient


@dataclass
class _Sent:
    body: dict


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> _Sent:
    """Capture the request body instead of calling the estate router."""
    captured = _Sent(body={})

    class _Response:
        def read(self) -> bytes:
            return json.dumps(
                {
                    "choices": [{"message": {"content": "ok"}}],
                    "usage": {"total_tokens": 7},
                }
            ).encode()

        def __enter__(self) -> "_Response":
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    def _urlopen(req, timeout=None):  # noqa: ANN001, ARG001
        captured.body = json.loads(req.data)
        return _Response()

    monkeypatch.setattr("otto.router.providers.urllib.request.urlopen", _urlopen)
    monkeypatch.setenv("LITELLM_API_KEY", "test-key")
    return captured


def test_the_default_budget_covers_a_reasoning_lane(
    sent: _Sent, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OTTO_ROUTER_MAX_TOKENS", raising=False)
    LiteLLMClient().complete("kimi", "hello", timeout_seconds=5)
    assert sent.body["max_tokens"] == _DEFAULT_MAX_TOKENS
    assert _DEFAULT_MAX_TOKENS >= 4096, "a 200-token cap returned an empty answer"


def test_the_budget_is_set_by_the_deployment(
    sent: _Sent, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OTTO_ROUTER_MAX_TOKENS", "512")
    LiteLLMClient().complete("kimi", "hello", timeout_seconds=5)
    assert sent.body["max_tokens"] == 512


@pytest.mark.parametrize("raw", ["0", "-1", "not-a-number", ""])
def test_an_unusable_override_falls_back_to_the_default(
    sent: _Sent, monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv("OTTO_ROUTER_MAX_TOKENS", raw)
    LiteLLMClient().complete("kimi", "hello", timeout_seconds=5)
    assert sent.body["max_tokens"] == _DEFAULT_MAX_TOKENS
