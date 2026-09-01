"""Shared fixtures for the W3 demo-command BDD suite (crew#768).

``ctx`` is the one mutable dict Given/When/Then steps share within a
scenario, the same idiom as the other otto lanes' suites.
"""

from __future__ import annotations

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "demo: W3 demo-command scenarios (the matrix cannot lie)"
    )


@pytest.fixture
def ctx() -> dict:
    return {}
