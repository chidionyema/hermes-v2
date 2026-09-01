"""Shared fixtures for the boot lane test suite.

Every test here runs under ``OTTO_OBS_MODE=test`` (``otto/obs/config.py``'s
named escape): observability binds to an in-memory exporter, so no test
needs a real OTLP collector. Same store-hygiene idiom as
``otto/tests/integration/test_smoke_assembly.py``: a fresh
``InMemorySpanExporter`` before and after every test, so a `shutdown()``
in one test can never poison the shared store for the next.
"""

from __future__ import annotations

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from otto.obs.config import MODE_ENV, MODE_TEST
from otto.obs.export import obs_test_store


def _reset_shared_store() -> None:
    store = obs_test_store()
    store.clear()
    store.span_exporter = InMemorySpanExporter()


@pytest.fixture(autouse=True)
def _obs_test_mode(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(MODE_ENV, MODE_TEST)
    _reset_shared_store()
    yield
    _reset_shared_store()
