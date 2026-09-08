"""Shared fixtures for the CP2b surface-contract BDD suite.

``ctx`` is the one piece of mutable state Given/When/Then steps pass
between each other within a scenario, the same dict-fixture idiom the
CP2 gateway-core suite uses (``otto/tests/cp2/conftest.py``).
"""

from __future__ import annotations

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "cp2b: CP2b surface-contract scenarios")
    config.addinivalue_line(
        "markers", "surface_contract: channel-plane adapter contract feature"
    )


@pytest.fixture
def ctx() -> dict:
    return {}
