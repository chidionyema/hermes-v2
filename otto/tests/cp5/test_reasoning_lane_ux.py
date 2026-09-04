"""The three things a 30-second reasoning lane needs before it is usable
from Telegram, all of which the founder named on 2026-09-04 (record:
`~/.claude/docs/founder/2026-09-04T0605Z-that-is-elite-execution-you-diagnosed-the-root-a7d3f1d7.md`):

* the sender is told the bot is composing, not left reading silence;
* the operator can send a message to the reasoning lane deliberately;
* a lane that narrates its way to the answer still parses.

No socket is opened here: the transport is a recording fake, which is the
same pattern the rest of `otto/tests` already uses.
"""

from __future__ import annotations

import threading
import time

import pytest

from otto.boot.pipeline import DEEP_TASK_CLASS, route_hint
from otto.boot.presence import typing_while
from otto.router.config import RouterConfig
from otto.router.contract import MalformedProviderOutput, extract_json_object
from otto.router.core import Router, RouterTask
from otto.router.budget import BudgetLedger
from otto.router.core import InMemoryNotifier


class RecordingTransport:
    """Counts chat actions and messages; never opens a socket."""

    def __init__(self) -> None:
        self.actions: list[tuple[int, str]] = []
        self.messages: list[tuple[int, str]] = []
        self.lock = threading.Lock()

    def send_message(self, chat_id: int, text: str) -> None:
        self.messages.append((chat_id, text))

    def set_webhook(self, url: str) -> None:  # pragma: no cover - not exercised
        raise AssertionError("the webhook is not set from this test")

    def send_chat_action(self, chat_id: int, action: str = "typing") -> None:
        with self.lock:
            self.actions.append((chat_id, action))


class ExplodingTransport(RecordingTransport):
    def send_chat_action(self, chat_id: int, action: str = "typing") -> None:
        raise RuntimeError("telegram said no")


# -- the sender is told the bot is composing ---------------------------------


def test_the_typing_indicator_fires_before_the_work_finishes() -> None:
    transport = RecordingTransport()
    with typing_while(transport, 42):
        pass
    assert transport.actions == [(42, "typing")]


def test_the_indicator_is_refreshed_for_a_call_longer_than_telegram_holds_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Telegram clears the action after ~5s, so one call is not enough for a
    30-second answer. The refresh interval is shortened here so the test
    measures the loop rather than the clock."""
    monkeypatch.setattr("otto.boot.presence._REFRESH_SECONDS", 0.01)
    transport = RecordingTransport()
    with typing_while(transport, 7):
        time.sleep(0.1)
    with transport.lock:
        assert len(transport.actions) > 1, transport.actions


def test_no_reply_address_means_no_indicator() -> None:
    transport = RecordingTransport()
    with typing_while(transport, None):
        pass
    assert transport.actions == []


def test_a_failing_indicator_never_costs_the_sender_the_answer() -> None:
    """The block must complete even when Telegram refuses the action."""
    transport = ExplodingTransport()
    ran = False
    with typing_while(transport, 9):
        ran = True
    assert ran


# -- the operator can reach the reasoning lane deliberately ------------------


@pytest.mark.parametrize("prefix", ["/think", "/kimi"])
def test_a_prefix_sends_the_message_to_the_deep_lane(prefix: str) -> None:
    task_class, message = route_hint(f"{prefix} why did the router refuse that")
    assert task_class == DEEP_TASK_CLASS
    assert message == "why did the router refuse that"


def test_an_ordinary_message_is_untouched() -> None:
    assert route_hint("what is the estate spending today") == (
        "research",
        "what is the estate spending today",
    )


def test_a_prefix_that_is_only_the_start_of_a_word_is_a_question() -> None:
    task_class, message = route_hint("/thinking about lunch")
    assert task_class == "research"
    assert message == "/thinking about lunch"


def test_the_deep_class_routes_to_the_reasoning_model() -> None:
    config = RouterConfig()
    router = Router(
        config=config, ledger=BudgetLedger(config=config), notifier=InMemoryNotifier()
    )
    lane = router.route(
        RouterTask(input="anything", source="telegram", task_class=DEEP_TASK_CLASS)
    )
    assert lane == "deep"
    assert config.lanes["deep"].model == "kimi"


def test_the_reasoning_model_has_a_known_family() -> None:
    """An unmapped model refuses at validation, so the alias needs an entry."""
    assert RouterConfig().family_of("kimi") == "moonshot"


# -- a lane that narrates still parses ---------------------------------------


def test_thinking_around_the_object_is_stripped_like_any_other_framing() -> None:
    raw = (
        'Let me work through this.\n{"answer": "yes", "claims": []}\nThat is my answer.'
    )
    assert extract_json_object(raw) == {"answer": "yes", "claims": []}


def test_a_brace_inside_a_string_does_not_cut_the_object_short() -> None:
    raw = 'Thinking...\n{"answer": "use a { brace", "claims": []}'
    assert extract_json_object(raw)["answer"] == "use a { brace"


def test_prose_with_no_object_still_refuses() -> None:
    with pytest.raises(MalformedProviderOutput):
        extract_json_object("I thought about it and decided not to answer.")
