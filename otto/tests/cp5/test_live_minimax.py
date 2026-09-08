"""Live integration: one real bulk-lane request through the router to lane
``minimax`` on the estate model router (LiteLLM), asserting the response
normalises into the universal contract with verification UNVERIFIED.

Skips cleanly — never fails — when the router is unreachable or the key
file is absent, mirroring ``bin/consult``'s exit-3 philosophy: an
unreachable estate router is normal (the laptop may be off the network),
not an error. Configuration is environment only: LITELLM_BASE_URL,
LITELLM_API_KEY (else the named secrets.d file). No secret value is ever
printed by this test.

The instruction string below is test fixture data describing the JSON
schema the contract requires — not a platform prompt (R64 note in the
package docstring).
"""

from __future__ import annotations

import pytest

from otto.router import (
    BudgetLedger,
    InMemoryNotifier,
    OutcomeState,
    Router,
    RouterConfig,
    RouterTask,
    VerificationStatus,
)
from otto.router.providers import LiteLLMClient, litellm_reachable
from otto.router.ulid import is_ulid

_SCHEMA_REQUEST = (
    "Answer with a single raw JSON object and nothing else - no prose, no "
    "markdown fence. The object must have exactly these keys: "
    '"answer" (string, one sentence naming the capital of France), '
    '"claims" (array with exactly one object: {"text": string, '
    '"evidence_refs": [], "confidence": "low"}), '
    '"proposed_actions" (empty array), "unknowns" (empty array).'
)


@pytest.mark.live
def test_bulk_lane_minimax_normalises_to_unverified_contract() -> None:
    if not litellm_reachable():
        pytest.skip("estate model router unreachable or no key: normal, not an error")

    config = RouterConfig()
    notifier = InMemoryNotifier()
    router = Router(
        config=config, ledger=BudgetLedger(config=config), notifier=notifier
    )
    # source=cron routes to the bulk lane, whose model is minimax by default.
    task = RouterTask(input=_SCHEMA_REQUEST, source="cron")
    assert router.route(task) == "bulk"
    assert config.lanes["bulk"].model == "minimax"

    outcome = router.execute(task, LiteLLMClient())

    if outcome.state is OutcomeState.NEEDS_HUMAN:
        pytest.skip(f"provider unavailable mid-call: {outcome.reason}")

    assert outcome.state is OutcomeState.COMPLETED_UNVERIFIED, outcome.reason
    response = outcome.response
    assert response is not None
    # The universal contract, filled by a real minimax completion:
    assert isinstance(response.answer, str) and response.answer
    assert len(response.claims) >= 1
    assert response.verification is VerificationStatus.UNVERIFIED
    assert response.lane == "bulk"
    assert response.model == "minimax"
    assert is_ulid(response.task_id)
    assert response.tokens > 0
    assert response.cost_usd >= 0.0
    # And the spend landed on the lane ledger.
    assert router.ledger.spent("bulk") == outcome.charged_usd
