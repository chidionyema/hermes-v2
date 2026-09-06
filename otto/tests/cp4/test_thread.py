"""Step 8 (hands & senses): the door's conversation thread per principal.

Spec ``docs/specs/otto-door-hands-and-senses.md`` step 8 done-command: three
envelopes from one principal produce a provider call whose ``messages`` holds
the earlier two turns, and a fourth envelope from another principal holds none
of them.

Purely offline: these run against the real store path on an in-memory SQLite
(the same SQL the Postgres store runs), so they prove the actual queries and
the ``thread_messages`` projection without a Postgres cluster or a socket.

The answering-loop shape here mirrors ``otto.boot.pipeline.answer_envelope``:
for every inbound envelope, ``append`` its (user) turn and the reply
(assistant) turn, then build the next provider ``messages`` from the live
thread.

Untrusted senders get their own thread — the tests here use principals that
are the allow-listed ids, and one assertion proves an unrecognised (other)
principal never sees the founder's history. Identity itself stays the
surface-binding law (``otto/surface/identity.py``); this module only keys the
thread by whatever principal the binding resolved.
"""

from __future__ import annotations

import sqlite3

from otto.ingress.thread import (
    SqliteConversationStore,
    Turn,
    thread_messages,
)

PRINCIPAL = "principal:memex-founder#tg-1001"
OTHER = "principal:stranger#tg-9999"


def _store() -> SqliteConversationStore:
    return SqliteConversationStore(sqlite3.connect(":memory:"))


def _answer_cycle(
    store, principal: str, q: str, a: str, surface: str = "telegram"
) -> str:
    """One envelope + its reply, the way ``answer_envelope`` would record
    it. Returns the thread id the turns landed on."""
    tid = store.append(principal, surface, role="user", content=q)
    store.append(principal, surface, role="assistant", content=a)
    return tid


def test_history_of_three_envelopes_holds_the_earlier_two():
    store = _store()
    _answer_cycle(store, PRINCIPAL, "list the files in otto", "a, b, c")
    _answer_cycle(store, PRINCIPAL, "which is largest", "b is the largest")
    thread = store.thread_for(PRINCIPAL)
    assert thread is not None

    # The third question becomes the live ``current``; the earlier two
    # turns must already be in the provider message list.
    messages = thread_messages(thread, current="and the second file?")
    user_turns = [m for m in messages if m["role"] == "user"]
    assert any("list the files in otto" in m["content"] for m in user_turns)
    assert any("which is largest" in m["content"] for m in user_turns)
    assert messages[-1] == {"role": "user", "content": "and the second file?"}
    # The earlier assistant reply travelled with its user turn.
    assert any("b is the largest" in m["content"] for m in messages)
    # The assistant text "a, b, c" is the first assistant turn, present.
    assistants = [m for m in messages if m["role"] == "assistant"]
    assert any("a, b, c" in m["content"] for m in assistants)


def test_a_second_principal_holds_none_of_the_firsts_history():
    store = _store()
    _answer_cycle(store, PRINCIPAL, "remember the code is in otto", "kept")
    other_thread = store.thread_for(OTHER)
    # An unrecognised principal starts cold: no thread, and a provider call
    # carries only its own current message, never the founder's turns.
    messages = thread_messages(other_thread, current="hi there")
    assert messages == [{"role": "user", "content": "hi there"}]
    body = "".join(m.get("content", "") for m in messages)
    assert "remember the code is in otto" not in body


def test_live_thread_is_recent_only_after_an_answer_lands():
    store = _store()
    _answer_cycle(store, PRINCIPAL, "q1", "a1")
    _answer_cycle(store, PRINCIPAL, "q2", "a2")
    thread = store.thread_for(PRINCIPAL)
    messages = thread_messages(thread, current="q3")
    texts = " ".join(m.get("content", "") for m in messages)
    # q1/a1 and q2/a2 are intact and ordered; q3 is the tail.
    assert texts.index("q1") < texts.index("q2")
    assert texts.index("q2") < texts.index("q3")
    assert messages[-1] == {"role": "user", "content": "q3"}


def test_no_history_yields_just_the_current_message():
    store = _store()
    messages = thread_messages(
        store.thread_for(PRINCIPAL), current="first ever question"
    )
    assert messages == [{"role": "user", "content": "first ever question"}]


def test_memory_context_slots_between_history_and_current():
    store = _store()
    _answer_cycle(store, PRINCIPAL, "q1", "a1")
    messages = thread_messages(
        store.thread_for(PRINCIPAL),
        memory_context="chidi likes a flat white",
        current="make it two",
    )
    # order: history turn(s), memory context block, current question.
    labels = [m.get("content", "") for m in messages]
    q1 = labels.index("q1")
    mem = next(i for i, c in enumerate(labels) if "flat white" in c)
    cur = next(i for i, c in enumerate(labels) if c == "make it two")
    assert q1 < mem < cur
    assert cur == len(labels) - 1


def test_new_topic_starts_fresh_without_losing_the_old():
    store = _store()
    first_id = _answer_cycle(store, PRINCIPAL, "topic 1 question", "old answer")
    fresh = store.new_topic(PRINCIPAL)
    assert fresh != first_id
    # the old thread's turns are untouched (nothing is deleted), the fresh
    # id is what the next live turn lands on.
    store.append(PRINCIPAL, "telegram", role="user", content="fresh question")
    thread = store.thread_for(PRINCIPAL)
    assert thread is not None
    assert thread.thread_id == fresh
    assert [t.content for t in thread.turns] == ["fresh question"]


def test_turns_pair_user_and_assistant_in_order():
    store = _store()
    store.append(PRINCIPAL, "telegram", role="user", content="q1")
    store.append(PRINCIPAL, "portal", role="assistant", content="a1 over the portal")
    thread = store.thread_for(PRINCIPAL)
    assert thread is not None
    messages = thread_messages(thread, current="and now?")
    roles = [m["role"] for m in messages]
    # a user/assistant pair from the history, then a closing user question.
    assert roles.count("user") == 2
    assert roles.count("assistant") == 1
    assert roles[-1] == "user"


def test_surface_is_a_column_not_a_scope():
    """Same principal, different surfaces, same thread (crew#773: the
    conversation follows across Telegram → portal → voice)."""
    store = _store()
    store.append(PRINCIPAL, "telegram", role="user", content="over telegram")
    store.append(PRINCIPAL, "portal", role="user", content="over the portal")
    thread = store.thread_for(PRINCIPAL)
    assert thread is not None
    turns = {
        surface: [t for t in thread.turns if t.surface == surface]
        for surface in ("telegram", "portal")
    }
    assert turns["telegram"] and turns["portal"]
    bodies = " ".join(t.content for t in thread.turns)
    assert bodies.index("over telegram") < bodies.index("over the portal")


def test_turn_as_message_uses_openai_shape():
    turn = Turn(
        thread_id="th_x",
        principal=PRINCIPAL,
        surface="telegram",
        role="user",
        content="hello otto",
    )
    assert turn.as_message == {"role": "user", "content": "hello otto"}
