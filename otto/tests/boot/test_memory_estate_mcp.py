"""The estate's unified memory over MCP: what it sends, and what it does without one."""

from __future__ import annotations

import json
import time

import pytest

from otto.memory import estate_mcp


class _Resp:
    def __init__(self, body: str, session_id: str | None = None) -> None:
        self._body = body.encode("utf-8")
        self.headers = {"mcp-session-id": session_id} if session_id else {}

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_Resp":
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


def _tool_result(payload: dict, *, is_error: bool = False) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 2,
        "result": {
            "content": [{"type": "text", "text": json.dumps(payload)}],
            "isError": is_error,
        },
    }


@pytest.fixture
def cfg(tmp_path) -> estate_mcp.EstateMemoryConfig:
    key = tmp_path / "ESTATE_MCP_KEY"
    key.write_text("secret-key\n")
    return estate_mcp.config(
        {
            estate_mcp.URL_ENV: "http://agentgateway.mcp.svc:3000/estate/mcp",
            estate_mcp.KEY_FILE_ENV: str(key),
        }
    )


def _fake_server(monkeypatch: pytest.MonkeyPatch, tool_body: str) -> list:
    """Answers initialize with a session id, then tools/call with ``tool_body``."""
    seen: list = []

    def fake_urlopen(req, timeout):
        payload = json.loads(req.data)
        seen.append((payload, dict(req.header_items()), timeout))
        if payload["method"] == "initialize":
            return _Resp(json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}), "sess-1")
        return _Resp(tool_body)

    monkeypatch.setattr(estate_mcp.urllib.request, "urlopen", fake_urlopen)
    return seen


def test_off_without_either_setting_is_a_no_op(tmp_path) -> None:
    for env in (
        {},
        {estate_mcp.URL_ENV: "http://gw:3000/estate/mcp"},
        {estate_mcp.KEY_FILE_ENV: str(tmp_path / "missing")},
        {
            estate_mcp.URL_ENV: "http://gw:3000/estate/mcp",
            estate_mcp.KEY_FILE_ENV: str(tmp_path / "missing"),
        },
        {estate_mcp.URL_ENV: "file:///etc/passwd", estate_mcp.KEY_FILE_ENV: __file__},
    ):
        cfg = estate_mcp.config(env)
        assert not cfg.enabled
        assert estate_mcp.brief("anything", cfg=cfg) == ""
        assert estate_mcp.recall("anything", cfg=cfg) == []
        assert estate_mcp.remember("anything", cfg=cfg) is False


def test_brief_handshakes_then_calls_the_tool(monkeypatch, cfg) -> None:
    seen = _fake_server(
        monkeypatch,
        json.dumps(
            _tool_result(
                {
                    "relevant": [{"text": "otto-gateway reads ESTATE_MCP_KEY", "metadata": {}}],
                    "recent": [
                        {"text": "otto-gateway reads ESTATE_MCP_KEY", "metadata": {}},
                        {"text": "decided: one memory for every agent", "metadata": {}},
                    ],
                }
            )
        ),
    )
    text = estate_mcp.brief("how does memory reach numun", cfg=cfg)
    assert text == (
        "- otto-gateway reads ESTATE_MCP_KEY\n- decided: one memory for every agent"
    )
    (init, init_headers, _), (call, call_headers, timeout) = seen
    assert init["method"] == "initialize"
    assert init["params"]["protocolVersion"] == estate_mcp.PROTOCOL_VERSION
    assert init_headers["Authorization"] == "Bearer secret-key"
    assert "text/event-stream" in init_headers["Accept"]
    assert call["method"] == "tools/call"
    assert call["params"] == {
        "name": "brief",
        "arguments": {"task": "how does memory reach numun", "limit": estate_mcp.BRIEF_LIMIT},
    }
    # urllib title-cases header names it stores.
    assert call_headers["Mcp-session-id"] == "sess-1"
    assert 0 < timeout <= estate_mcp.BRIEF_TIMEOUT_S


def test_sse_response_reads_the_last_data_line(monkeypatch, cfg) -> None:
    body = (
        "event: message\n"
        "data: {\"jsonrpc\": \"2.0\", \"method\": \"notifications/progress\"}\n\n"
        "event: message\n"
        f"data: {json.dumps(_tool_result({'memories': [{'text': 'one', 'metadata': {}}]}))}\n\n"
    )
    _fake_server(monkeypatch, body)
    assert estate_mcp.recall("one", cfg=cfg) == [{"text": "one", "metadata": {}}]


def test_remember_sends_who_wrote_it(monkeypatch, cfg) -> None:
    seen = _fake_server(monkeypatch, json.dumps(_tool_result({"id": "m-1"})))
    assert estate_mcp.remember("he asked about the vault", subject="otto-numun/telegram", cfg=cfg)
    args = seen[-1][0]["params"]["arguments"]
    assert args == {
        "content": "he asked about the vault",
        "kind": "fact",
        "source": "telegram",
        "runtime": "otto-numun",
        "subject": "otto-numun/telegram",
    }


def test_tool_error_is_nothing_not_a_raise(monkeypatch, cfg) -> None:
    _fake_server(monkeypatch, json.dumps(_tool_result({"detail": "boom"}, is_error=True)))
    assert estate_mcp.brief("anything", cfg=cfg) == ""
    assert estate_mcp.remember("anything", cfg=cfg) is False


def test_unreachable_or_slow_memory_never_costs_the_answer(monkeypatch, cfg) -> None:
    def boom(*_a, **_k):
        raise TimeoutError("timed out")

    monkeypatch.setattr(estate_mcp.urllib.request, "urlopen", boom)
    assert estate_mcp.brief("anything", cfg=cfg) == ""
    assert estate_mcp.recall("anything", cfg=cfg) == []
    assert estate_mcp.remember("anything", cfg=cfg) is False

    def refused(*_a, **_k):
        raise estate_mcp.urllib.error.URLError("connection refused")

    monkeypatch.setattr(estate_mcp.urllib.request, "urlopen", refused)
    assert estate_mcp.brief("anything", cfg=cfg) == ""


def test_both_round_trips_share_one_deadline(monkeypatch, cfg) -> None:
    """A slow initialize leaves the call only what is left of the budget."""
    timeouts: list[float] = []

    def slow_urlopen(req, timeout):
        timeouts.append(timeout)
        if json.loads(req.data)["method"] == "initialize":
            time.sleep(0.05)
            return _Resp(json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}), "s")
        return _Resp(json.dumps(_tool_result({"relevant": []})))

    monkeypatch.setattr(estate_mcp.urllib.request, "urlopen", slow_urlopen)
    estate_mcp.brief("anything", cfg=cfg)
    assert timeouts[1] < timeouts[0] <= estate_mcp.BRIEF_TIMEOUT_S


def test_remember_in_background_does_not_start_when_off(monkeypatch) -> None:
    monkeypatch.delenv(estate_mcp.URL_ENV, raising=False)
    monkeypatch.delenv(estate_mcp.KEY_FILE_ENV, raising=False)
    assert estate_mcp.remember_in_background("anything") is None
