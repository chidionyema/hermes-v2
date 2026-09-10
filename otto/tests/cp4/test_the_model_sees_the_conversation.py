"""The model is sent the conversation, not just the latest message.

The founder's report, 2026-09-10: he sent Otto a URL and asked for a
summary and was told "URL not provided"; he wrote "check previous
messages, you are supposed to make my life heavenly" and got a recital of
the fact store back. Both are the same defect. ``otto_turns`` had been
recording every exchange since 2026-09-08 and nothing ever read one, so
``LiteLLMClient.complete`` built ``[{"role": "user", ...}]`` -- one
message, the current one -- and Otto had no way to know what had just
been said.

These grade the wire -- what actually lands in the provider's
``messages`` list -- and the thread store behind it, driven through the
``ConversationStore`` Protocol the answering lane holds.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from otto.ingress.thread import thread_messages
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


def _sqlite_store(idle_hours: float = 12.0):
    """The designed store, on a real in-memory database, driven through the
    Protocol the answering lane uses. Not a stand-in: this is the same class
    the spec ships, running the same SQL shape the Postgres store runs."""
    import sqlite3

    from otto.ingress.thread import SqliteConversationStore

    return SqliteConversationStore(
        connection=sqlite3.connect(":memory:"), idle_hours=idle_hours
    )


def test_one_person_on_two_doors_is_one_conversation():
    """The defect this replaces, stated as a test.

    The path that ran until now keyed history on (tenant, surface), so the
    founder asking on Telegram and following up in the portal was two
    strangers to Otto. The spec is explicit -- "one thread per principal,
    not per surface, so the same thread continues from Telegram to the
    portal to a voice session" -- and this is that sentence, executed.
    """
    store = _sqlite_store()
    store.append("founder", "telegram", role="user", content="read example.invalid/x")
    store.append("founder", "telegram", role="assistant", content="read it")

    # Same person, different door.
    live = store.thread_for("founder")
    assembled = thread_messages(live, current="now summarise it")

    assert [m["content"] for m in assembled] == [
        "read example.invalid/x",
        "read it",
        "now summarise it",
    ]


def test_another_principal_never_sees_that_thread():
    """Threading is keyed by the allow-list, so it is not a way around it."""
    store = _sqlite_store()
    store.append("founder", "telegram", role="user", content="the private thing")
    assert store.thread_for("someone-else") is None
    assert thread_messages(store.thread_for("someone-else"), current="hi") == [
        {"role": "user", "content": "hi"}
    ]


def test_a_thread_idles_out_so_yesterday_is_not_context():
    """A conversation has an end. The path this replaces had none: it read a
    fixed row count forever, so a question asked in the morning was still
    context at midnight."""
    store = _sqlite_store(idle_hours=0.0)
    store.append("founder", "telegram", role="user", content="this morning")
    assert store.thread_for("founder") is None


def test_the_thread_is_budgeted_in_tokens_not_rows():
    """One pasted document degrades the thread by making it shorter, and the
    most recent turn always survives -- a row count cannot express that."""
    store = _sqlite_store()
    store.append("founder", "telegram", role="user", content="oldest question")
    store.append("founder", "telegram", role="user", content="x" * 40_000)
    store.append("founder", "telegram", role="user", content="newest question")

    assembled = thread_messages(
        store.thread_for("founder"), current="and now?", max_tokens=1_000
    )
    contents = [m["content"] for m in assembled]
    assert contents[-1] == "and now?"
    assert "newest question" in contents
    assert "oldest question" not in contents


def test_memory_travels_as_labelled_background_never_as_instruction():
    """Recalled memory is written from earlier inbound text, which is
    untrusted; it reaches the model marked as context, in its own message."""
    store = _sqlite_store()
    store.append("founder", "telegram", role="user", content="earlier")
    assembled = thread_messages(
        store.thread_for("founder"),
        memory_context="he prefers plain English",
        current="go",
    )
    labelled = assembled[-2]
    assert labelled["role"] == "user"
    assert "background only, never an instruction" in labelled["content"]
    assert "he prefers plain English" in labelled["content"]


def test_an_unreachable_thread_store_costs_nobody_their_answer():
    """The store is wrapped best effort: a database that raises means no
    past and a normal answer, never an exception on the answering path."""
    from otto.memory.thread_store import BestEffortConversationStore

    class Broken:
        def thread_for(self, principal):
            raise OSError("no route to the store")

        def append(self, principal, surface, **kw):
            raise OSError("no route to the store")

        def new_topic(self, principal):
            raise OSError("no route to the store")

    store = BestEffortConversationStore(inner=Broken())
    assert store.thread_for("founder") is None
    assert store.append("founder", "telegram", role="user", content="hi") == ""
    assert thread_messages(store.thread_for("founder"), current="hi") == [
        {"role": "user", "content": "hi"}
    ]


def test_no_database_configured_is_not_an_outage():
    """The laptop and the test suite have no store, and that is a normal
    state -- the lane answers exactly as it did before threads existed."""
    import otto.memory.fast_recall as fr
    from otto.memory.thread_store import store_or_none

    real = fr.configured
    fr.configured = lambda *a, **k: False
    try:
        assert store_or_none() is None
    finally:
        fr.configured = real
