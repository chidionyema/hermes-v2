"""Otto's own reply contract is never what a person is handed.

The founder's Telegram history, 2026-09-10: one turn came back as *"The
reply protocol requires exactly one raw JSON object with keys: answer,
claims, proposed_actions, unknowns"*. That turn is recorded in
``otto_turns`` as ``completed_unverified`` with ``attempts=2`` -- so the
first reply was refused, the model was handed ``REPAIR_NOTE``, and on the
second attempt it answered the note instead of the founder. The reply
parsed, so nothing below the parser ever looked at it.

Two doors were open and both are shut here: the router now refuses a
repaired reply that restates the wire contract, and the refusal sentence a
person reads no longer carries the parser's own words.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from otto.boot.pipeline import _state_sentence
from otto.router.budget import BudgetLedger
from otto.router.config import RouterConfig
from otto.router.core import (
    InMemoryNotifier,
    OutcomeState,
    Router,
    RouterOutcome,
    RouterTask,
)
from otto.router.providers import ProviderResult


def _reply(answer: str, claims: list[str] | None = None) -> str:
    return json.dumps(
        {
            "answer": answer,
            "claims": [
                {"text": t, "evidence_refs": [], "confidence": "med"}
                for t in (claims or [])
            ],
            "proposed_actions": [],
            "unknowns": [],
        }
    )


# The exact words the founder was handed, as the answer of a reply that parses.
THE_FOUNDERS_TURN = _reply(
    "The reply protocol requires exactly one raw JSON object with keys: "
    "answer, claims, proposed_actions, unknowns."
)
# A first reply the parser refuses: an action shaped like a chat tool call.
REFUSED = json.dumps(
    {
        "answer": "Let me check.",
        "claims": [],
        "proposed_actions": [{"name": "kubectl", "arguments": {}}],
        "unknowns": [],
    }
)
ANSWERED = _reply("Three nodes are ready.", ["Three nodes are ready."])


@dataclass
class QueuedClient:
    bodies: list[str]
    prompts: list[str] = field(default_factory=list)

    def complete(
        self, model: str, payload: str, timeout_seconds: float
    ) -> ProviderResult:
        self.prompts.append(payload)
        return ProviderResult(text=self.bodies.pop(0), tokens=50)


def _router() -> Router:
    config = RouterConfig()
    return Router(
        config=config, ledger=BudgetLedger(config=config), notifier=InMemoryNotifier()
    )


def _task(text: str = "how many nodes?") -> RouterTask:
    return RouterTask(
        input=text, source="telegram", task_class="research", task_id="t1"
    )


def test_the_reply_the_founder_was_handed_is_now_refused():
    client = QueuedClient(bodies=[REFUSED, THE_FOUNDERS_TURN])
    outcome = _router().execute(_task(), client)
    assert outcome.state is OutcomeState.REFUSED_MALFORMED
    assert outcome.response is None, "the contract must not be delivered as an answer"


def test_a_repaired_reply_that_answers_is_still_delivered():
    client = QueuedClient(bodies=[REFUSED, ANSWERED])
    outcome = _router().execute(_task(), client)
    assert outcome.state is OutcomeState.COMPLETED_UNVERIFIED
    assert outcome.attempts == 2
    assert outcome.response is not None
    assert outcome.response.answer == "Three nodes are ready."


def test_a_first_reply_about_json_is_an_answer_not_an_echo():
    # Nobody was repaired here, so a genuine question about the format is
    # answered like any other question -- the guard only ever reads a reply
    # that followed the repair note.
    asked = "what keys does your reply protocol use? name proposed_actions too"
    client = QueuedClient(
        bodies=[
            _reply(
                "The reply protocol uses one raw JSON object with proposed_actions "
                "among its keys."
            )
        ]
    )
    outcome = _router().execute(_task(asked), client)
    assert outcome.state is OutcomeState.COMPLETED_UNVERIFIED
    assert outcome.response is not None


def test_one_stray_mention_in_a_repaired_answer_is_not_an_echo():
    client = QueuedClient(
        bodies=[REFUSED, _reply("Three nodes are ready, and I proposed no actions.")]
    )
    outcome = _router().execute(_task(), client)
    assert outcome.state is OutcomeState.COMPLETED_UNVERIFIED
    assert outcome.response is not None


def test_the_sentence_a_person_reads_carries_no_parser_words():
    outcome = RouterOutcome(
        state=OutcomeState.REFUSED_MALFORMED,
        lane="judgment",
        task_id="t1",
        reason=(
            "proposed_actions[0] is missing required key 'tool'; "
            "tier must be one of T0, T1, T2, T3"
        ),
    )
    sentence = _state_sentence(outcome).lower()
    for word in ("proposed_actions", "json", "parser", "tier", "t0"):
        assert word not in sentence, f"{word!r} leaked into the founder's reply"
    assert "ask again" in sentence


def test_a_state_that_is_about_the_world_keeps_its_reason():
    # "I could not reach the model" without "egress denied" tells him nothing
    # about whether to wait or to go and look.
    outcome = RouterOutcome(
        state=OutcomeState.NEEDS_HUMAN,
        lane="judgment",
        task_id="t1",
        reason="egress denied: api.example.invalid",
    )
    assert "egress denied" in _state_sentence(outcome)
