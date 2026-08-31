"""Otto CP5 — model router and structured outputs (crew#768, spec section 5).

Three lanes route by policy, every model call is normalised into one
universal response contract, unverified claims are flagged by the renderer
(never by the model), and budget guards queue-and-notify rather than
degrade silently.

R64 compliance: this package contains NO prompt templates. Every call the
router makes carries the caller's payload through unchanged; the one place
a fixed instruction string exists is the live integration test's request
for schema-shaped JSON, which is test fixture data, not a platform prompt.
When platform prompts arrive (a later checkpoint) they must be DSPy
programs, not hand-written strings.
"""

from otto.router.budget import BudgetLedger
from otto.router.config import LaneConfig, RetryPolicy, RouterConfig
from otto.router.contract import (
    Claim,
    MalformedProviderOutput,
    ProposedAction,
    RouterResponse,
    VerificationStatus,
    normalise_provider_output,
)
from otto.router.core import (
    InMemoryNotifier,
    Notifier,
    OutcomeState,
    Router,
    RouterOutcome,
    RouterTask,
)
from otto.router.evals import EvalGate, run_eval_cli
from otto.router.grounding import GroundingCheck
from otto.router.providers import (
    EgressDenied,
    ProviderClient,
    ProviderHTTPError,
    ProviderTimeout,
)
from otto.router.render import render_claims_for_telegram
from otto.router.ulid import new_ulid

__all__ = [
    "BudgetLedger",
    "Claim",
    "EgressDenied",
    "EvalGate",
    "GroundingCheck",
    "InMemoryNotifier",
    "LaneConfig",
    "MalformedProviderOutput",
    "Notifier",
    "OutcomeState",
    "ProposedAction",
    "ProviderClient",
    "ProviderHTTPError",
    "ProviderTimeout",
    "RetryPolicy",
    "Router",
    "RouterConfig",
    "RouterOutcome",
    "RouterResponse",
    "RouterTask",
    "VerificationStatus",
    "new_ulid",
    "normalise_provider_output",
    "render_claims_for_telegram",
    "run_eval_cli",
]


def boot(config=None):
    """W2 wiring (crew#768): this package's boot entrypoint.

    Instruments the component through ``otto.obs`` and returns the
    handle, or raises ``ObsBootError`` — nothing boots dark (LAW 50).
    The exporter endpoint comes only from ``OTEL_EXPORTER_OTLP_ENDPOINT``;
    ``OTTO_OBS_MODE=test`` binds in-memory exporters for suites.
    """
    from otto.obs import instrument

    return instrument("router", config)
