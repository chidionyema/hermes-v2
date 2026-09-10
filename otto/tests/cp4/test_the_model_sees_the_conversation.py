"""The model is sent the conversation, not just the latest message.

The founder's report, 2026-09-10: he sent Otto a URL and asked for a
summary and was told "URL not provided"; he wrote "check previous
messages, you are supposed to make my life heavenly" and got a recital of
the fact store back. Both are the same defect. ``otto_turns`` had been
recording every exchange since 2026-09-08 and nothing ever read one, so
``LiteLLMClient.complete`` built ``[{"role": "user", ...}]`` -- one
message, the current one -- and Otto had no way to know what had just
been said.

These grade the wire: what actually lands in the provider's ``messages``
list, and what ``recent_messages`` returns from real rows.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from otto.memory import conversation
from otto.router import BudgetLedger, InMemoryNotifier, Router, RouterConfig
from otto.router.core import RouterTask
from otto.router.providers import LiteLLMClient


class _Recorder(BaseHTTPRequestHandler):
    """A real HTTP endpoint in the LiteLLM reply shape that keeps the
    request body, so the test grades the bytes the client sent rather than
    a mock's call record."""

    seen: list[dict] = []

    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler's name
        body = self.rfile.read(int(self.headers["Content-Length"]))
        _Recorder.seen.append(json.loads(body))
        out = json.dumps(
            {
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                "usage": {"total_tokens": 1},
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):  # noqa: A003 - silence the test server
        pass


@pytest.fixture()
def router_endpoint(monkeypatch):
    _Recorder.seen = []
    srv = HTTPServer(("127.0.0.1", 0), _Recorder)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    monkeypatch.setenv("LITELLM_API_KEY", "sk-test-not-a-real-key")
    monkeypatch.setenv("OTTO_ROUTER_BASE_URL", f"http://127.0.0.1:{srv.server_port}/v1")
    monkeypatch.setenv("LITELLM_BASE_URL", f"http://127.0.0.1:{srv.server_port}/v1")
    yield srv
    srv.shutdown()


def test_the_conversation_reaches_the_provider_in_order(router_endpoint):
    """The turns arrive before the current message, oldest first."""
    history = [
        {"role": "user", "content": "read https://example.invalid/x and summarise"},
        {"role": "assistant", "content": "on it"},
    ]
    LiteLLMClient().complete(
        "some-lane-model", "well? summarise it", 30.0, history=history
    )
    assert _Recorder.seen, "the client made no HTTP call"
    sent = _Recorder.seen[-1]["messages"]
    assert [m["content"] for m in sent] == [
        "read https://example.invalid/x and summarise",
        "on it",
        "well? summarise it",
    ]
    assert [m["role"] for m in sent] == ["user", "assistant", "user"]


def test_no_history_sends_exactly_the_one_message_it_always_did(router_endpoint):
    """The old behaviour is intact for a caller that supplies no past."""
    LiteLLMClient().complete("some-lane-model", "hello", 30.0)
    assert _Recorder.seen[-1]["messages"] == [{"role": "user", "content": "hello"}]


def _router() -> Router:
    cfg = RouterConfig()
    return Router(
        config=cfg, ledger=BudgetLedger(config=cfg), notifier=InMemoryNotifier()
    )


def test_a_router_task_carrying_history_hands_it_to_the_client():
    """RouterTask.history reaches ProviderClient.complete, so the wiring
    between the answering lane and the provider is not just decoration."""
    got: dict = {}

    class Fake:
        def complete(self, model, payload, timeout_seconds, **kw):
            got.update(kw)
            from otto.router.providers import ProviderResult

            return ProviderResult(text="{}", tokens=1)

    past = ({"role": "user", "content": "earlier"},)
    _router().execute(RouterTask(input="now", history=past), Fake())
    assert got.get("history") == list(past)


def test_a_client_that_predates_history_is_called_the_old_way():
    """A fake with no ``history`` keyword must still work when the task
    carries none -- the compatibility rule the tool loop already follows."""
    calls: list[tuple] = []

    class OldFake:
        def complete(self, model, payload, timeout_seconds):
            calls.append((model, payload))
            from otto.router.providers import ProviderResult

            return ProviderResult(text="{}", tokens=1)

    _router().execute(RouterTask(input="now"), OldFake())
    assert calls, "the old-signature client was never called"


def test_history_is_budgeted_from_the_newest_turn_backwards():
    """A pasted document in one turn loses the oldest exchanges, never the
    most recent ones -- the newest turn is always present."""
    rows = [  # newest first, as the query returns them
        ("newest question", "newest answer"),
        ("x" * 5_000, "y" * 5_000),
        ("oldest question", "oldest answer"),
    ]

    class FakeCur:
        def execute(self, *a, **k):
            pass

        def fetchall(self):
            return rows

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class FakeConn:
        def cursor(self):
            return FakeCur()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    import otto.memory.db as db
    import otto.memory.fast_recall as fr

    real_connect, real_configured = db.connect, fr.configured
    db.connect = lambda *a, **k: FakeConn()
    fr.configured = lambda *a, **k: True
    try:
        msgs = conversation.recent_messages("t", "telegram", max_chars=2_000)
    finally:
        db.connect, fr.configured = real_connect, real_configured

    assert msgs[-2:] == [
        {"role": "user", "content": "newest question"},
        {"role": "assistant", "content": "newest answer"},
    ]
    assert all("oldest question" != m["content"] for m in msgs)


def test_an_unreachable_store_costs_nobody_their_answer():
    """History is best effort: a store that raises returns no past."""
    import otto.memory.db as db
    import otto.memory.fast_recall as fr

    real_connect, real_configured = db.connect, fr.configured

    def boom(*a, **k):
        raise OSError("no route to the store")

    db.connect, fr.configured = boom, (lambda *a, **k: True)
    try:
        assert conversation.recent_messages("t", "telegram") == []
    finally:
        db.connect, fr.configured = real_connect, real_configured
