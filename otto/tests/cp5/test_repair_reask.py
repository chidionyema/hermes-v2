"""A refused reply is asked for once more with the parser's reason, on the
same lane; a second refusal is final (2026-09-06 02:30Z, task
01M1T8VK56EE38TQ6TMT181BP6: the judgment lane sent a proposed action with no
tool name and the founder got the refusal text instead of an answer)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from otto.router.budget import BudgetLedger
from otto.router.config import RouterConfig
from otto.router.core import (
    REPAIR_NOTE,
    InMemoryNotifier,
    OutcomeState,
    Router,
    RouterTask,
)
from otto.router.providers import ProviderResult

GOOD = json.dumps(
    {
        "answer": "Three nodes are ready.",
        "claims": [
            {"text": "Three nodes are ready.", "evidence_refs": [], "confidence": "med"}
        ],
        "proposed_actions": [],
        "unknowns": [],
    }
)
# What the lane actually sent: an action shaped like a chat tool call, no "tool".
BAD = json.dumps(
    {
        "answer": "Let me check.",
        "claims": [],
        "proposed_actions": [{"name": "kubectl", "arguments": {"cmd": "get nodes"}}],
        "unknowns": [],
    }
)


@dataclass
class QueuedClient:
    bodies: list[str]
    prompts: list[str] = field(default_factory=list)
    models: list[str] = field(default_factory=list)

    def complete(
        self, model: str, payload: str, timeout_seconds: float
    ) -> ProviderResult:
        self.models.append(model)
        self.prompts.append(payload)
        return ProviderResult(text=self.bodies.pop(0), tokens=50)


def _router() -> Router:
    config = RouterConfig()
    return Router(
        config=config, ledger=BudgetLedger(config=config), notifier=InMemoryNotifier()
    )


def _task() -> RouterTask:
    return RouterTask(
        input="how many nodes?", source="telegram", task_class="research", task_id="t1"
    )


def test_one_bad_shape_is_asked_again_with_the_reason_and_then_answers():
    client = QueuedClient(bodies=[BAD, GOOD])
    outcome = _router().execute(_task(), client)
    assert outcome.state is OutcomeState.COMPLETED_UNVERIFIED
    assert outcome.attempts == 2
    assert (
        outcome.response is not None
        and outcome.response.answer == "Three nodes are ready."
    )
    # Same lane, same model, both times: never a fallback provider.
    assert len(set(client.models)) == 1
    # The second prompt is the first plus the parser's own reason.
    assert client.prompts[0] == "how many nodes?"
    assert client.prompts[1].startswith("how many nodes?")
    assert "proposed_actions[0].tool missing or not a string" in client.prompts[1]
    assert client.prompts[1].endswith(
        REPAIR_NOTE.format(
            reason="provider output refused: proposed_actions[0].tool missing or not a string"
        )
    )


def test_two_bad_shapes_are_refused_and_both_are_charged():
    client = QueuedClient(bodies=[BAD, BAD])
    router = _router()
    outcome = router.execute(_task(), client)
    assert outcome.state is OutcomeState.REFUSED_MALFORMED
    assert outcome.attempts == 2
    assert outcome.response is None
    assert outcome.charged_usd > 0
    assert router.ledger.spent(outcome.lane) == outcome.charged_usd
    assert len(client.prompts) == 2
