"""Spec step 2: the provider tool loop.

A ``LiteLLMClient.complete`` call given ``tools`` + ``tool_executor`` keeps
POSTing while the model asks to call a tool: it hands each ``tool_calls``
function to the executor, appends the ``role=tool`` result, and asks again,
stopping at the first text-only reply. The executor is the gateway bridge in
the real pipeline; here it is a recording stand-in so the tests can assert
exactly how many times the gateway ran and that a *denied* result is what the
model sees in the tool-turn message, never a hallucinated success.
"""

from __future__ import annotations

import json

import pytest

from otto.router.providers import LiteLLMClient

TOOLS = [
    {"type": "function", "function": {"name": "estate_find_entity", "parameters": {}}}
]


def _attach(monkeypatch: pytest.MonkeyPatch, responses: list[dict]) -> list[dict]:
    """Stub ``urllib.request.urlopen`` to record every outbound body and
    answer each HTTP turn from ``responses`` in order.

    The final entry is the terminal text-only reply; a test that supplies
    one tool-call turn of request N gives responses=[tool_call, final_text].
    ``urlopen`` is called exactly ``len(responses)`` times.
    """

    captured: list[dict] = []
    queue = list(responses)
    terminal = {
        "choices": [{"message": {"content": "final"}}],
        "usage": {"total_tokens": 3},
    }
    # ``complete`` refuses to egress without a key (``_read_key``), and CI
    # has none in its environment — but these tests never egress, they
    # monkeypatch ``urlopen`` below. A fixed fake key satisfies the guard so
    # every HTTP turn is answered by the stub above, not refused first.
    monkeypatch.setenv("LITELLM_API_KEY", "test-old-do-not-use")

    class _Resp:
        def read(self) -> bytes:
            return json.dumps(queue.pop(0) if queue else terminal).encode()

        def __enter__(self) -> "_Resp":
            return self

        def __exit__(self, *a: object) -> None:
            return None

    def _fake_open(req, timeout: float = 1.0):  # noqa: ARG001
        captured.append(json.loads(req.data.decode()))
        return _Resp()

    monkeypatch.setattr("urllib.request.urlopen", _fake_open)
    return captured


def _tool_call_response() -> dict:
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "estate_find_entity",
                                "arguments": '{"term": "spine"}',
                            },
                        }
                    ],
                }
            }
        ],
        "usage": {"total_tokens": 7},
    }


def _text_response(text: str, tokens: int = 3) -> dict:
    return {
        "choices": [{"message": {"content": text}}],
        "usage": {"total_tokens": tokens},
    }


def test_single_tool_turn_then_final_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One tool call, then text: the gateway stand-in runs exactly once and
    the final HTTP reply's text is what comes back."""
    captured = _attach(monkeypatch, [_tool_call_response(), _text_response("final")])

    executed: list[str] = []

    def executor(name: str, args: str) -> str:  # noqa: ARG001
        executed.append(name)
        return "found"

    result = LiteLLMClient().complete(
        "kimi", "find it", timeout_seconds=5, tools=TOOLS, tool_executor=executor
    )

    # The gateway (executor) was called once, with the requested tool.
    assert executed == ["estate_find_entity"]
    # The final answer is the text-only reply, not the tool result.
    assert result.text == "final"
    # Two HTTP turns: the tool-call request and its follow-up.
    assert len(captured) == 2
    # The follow-up carries the assistant's tool_calls plus the role=tool
    # result so the model knows what the tool returned.
    roles = [m["role"] for m in captured[1]["messages"]]
    assert roles == ["user", "assistant", "tool"]
    assert captured[1]["messages"][2]["tool_call_id"] == "call_1"
    assert captured[1]["messages"][2]["content"] == "found"


def test_denied_tool_messages_reach_the_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A T3 write refused by the gateway executor comes back to the model as
    ``denied: <reason>`` text — it must tell the sender what it could not do,
    never assert the delete happened."""
    denied_tc = _tool_call_response()
    msg = denied_tc["choices"][0]["message"]["tool_calls"][0]
    msg["id"] = "call_x"
    msg["function"]["name"] = "terminal_irreversible"
    msg["function"]["arguments"] = '{"command": "rm -rf /"}'
    captured = _attach(monkeypatch, [denied_tc, _text_response("final")])

    def executor(name: str, args: str) -> str:  # noqa: ARG001
        return "denied: HUMAN_APPROVAL_REFUSED"

    result = LiteLLMClient().complete(
        "kimi", "delete it all", timeout_seconds=5, tools=TOOLS, tool_executor=executor
    )
    assert result.text == "final"
    tool_msg = captured[1]["messages"][2]
    assert tool_msg["role"] == "tool"
    assert tool_msg["content"] == "denied: HUMAN_APPROVAL_REFUSED"


def test_no_tools_preserves_plain_single_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without tools the client is unchanged: one plain completion, no tools
    field, and token accounting matches the pre-step contract."""
    captured = _attach(monkeypatch, [_text_response("just text", 41)])
    result = LiteLLMClient().complete("kimi", "hello", timeout_seconds=5)
    assert result.text == "just text"
    assert result.tokens == 41
    assert len(captured) == 1
    assert "tools" not in captured[0]


def test_tools_require_executor(monkeypatch: pytest.MonkeyPatch) -> None:
    """tools without an executor is a caller bug the client refuses loudly."""
    _attach(monkeypatch, [_text_response("ok")])
    with pytest.raises(ValueError):
        LiteLLMClient().complete("kimi", "hi", timeout_seconds=5, tools=TOOLS)
