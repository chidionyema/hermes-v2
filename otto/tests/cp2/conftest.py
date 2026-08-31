"""Shared fixtures for the CP2 gateway-core BDD suite.

``ctx`` is the one piece of mutable state Given/When/Then steps pass
between each other within a scenario (the pytest-bdd idiom for this is
either fixture chaining via ``target_fixture`` or one shared mutable
dict — a dict is used here because the scenarios in this feature pass
five or six different objects around and a dict keeps every step
signature short).
"""

from __future__ import annotations

import pytest


def pytest_configure(config: pytest.Config) -> None:
    # Gherkin tags on the feature become pytest marks (pytest-bdd); register
    # them locally so this suite's own run has no unregistered-mark warning.
    config.addinivalue_line("markers", "cp2: CP2 tool-gateway core scenarios")
    config.addinivalue_line("markers", "gateway_core: gateway-core BDD feature")


@pytest.fixture
def ctx() -> dict:
    return {}
