"""Shared fixtures for the CP5 router BDD suite.

``ctx`` is the one mutable dict Given/When/Then steps pass between each
other within a scenario (same idiom as the CP2 suite). Fake provider
clients live here because both feature files use them.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

import pytest

from otto.router import (
    BudgetLedger,
    InMemoryNotifier,
    Router,
    RouterConfig,
)
from otto.router.providers import (
    EgressDenied,
    ProviderHTTPError,
    ProviderResult,
    ProviderTimeout,
)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "cp5: CP5 router and structured outputs")
    config.addinivalue_line(
        "markers", "live: talks to the estate model router; skips when unreachable"
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Deselect live tests by default so a plain run counts only hermetic
    tests. Opt in with OTTO_CP5_LIVE=1 (an env var, not a CLI option: a
    non-root conftest cannot register options, and this suite must behave
    the same from any invocation directory)."""
    if os.environ.get("OTTO_CP5_LIVE") == "1":
        return
    live = [item for item in items if "live" in item.keywords]
    if live:
        config.hook.pytest_deselected(items=live)
        items[:] = [item for item in items if "live" not in item.keywords]


@pytest.fixture
def ctx() -> dict:
    return {}


@pytest.fixture
def router_config() -> RouterConfig:
    return RouterConfig()


@pytest.fixture
def notifier() -> InMemoryNotifier:
    return InMemoryNotifier()


@pytest.fixture
def ledger(router_config: RouterConfig) -> BudgetLedger:
    return BudgetLedger(config=router_config)


@pytest.fixture
def router(
    router_config: RouterConfig, ledger: BudgetLedger, notifier: InMemoryNotifier
) -> Router:
    return Router(config=router_config, ledger=ledger, notifier=notifier)


def contract_json(
    answer: str = "ok",
    claims: list[dict] | None = None,
) -> str:
    """A well-formed universal-contract document as provider text."""
    return json.dumps(
        {
            "answer": answer,
            "claims": claims if claims is not None else [],
            "proposed_actions": [],
            "unknowns": [],
        }
    )


@dataclass
class ScriptedClient:
    """A provider client that raises the scripted failures, in order, then
    (if a body is scripted) answers. Records every model it was asked for
    so a test can prove no fallback provider was ever consulted."""

    failures: list[Exception] = field(default_factory=list)
    body: str | None = None
    tokens: int = 100
    calls: list[str] = field(default_factory=list)

    def complete(
        self, model: str, payload: str, timeout_seconds: float
    ) -> ProviderResult:
        self.calls.append(model)
        if self.failures:
            raise self.failures.pop(0)
        if self.body is None:
            raise AssertionError("ScriptedClient called with nothing scripted")
        return ProviderResult(text=self.body, tokens=self.tokens)


def always_5xx() -> ScriptedClient:
    return ScriptedClient(
        failures=[
            ProviderHTTPError(503),
            ProviderHTTPError(502),
            ProviderHTTPError(503),
            ProviderHTTPError(500),
        ]
    )


def always_timeout() -> ScriptedClient:
    return ScriptedClient(
        failures=[ProviderTimeout("read timeout")] * 4,
    )


def egress_denied() -> ScriptedClient:
    return ScriptedClient(failures=[EgressDenied("connection refused by policy")] * 4)
