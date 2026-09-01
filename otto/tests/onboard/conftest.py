"""Shared fixtures for the W4 onboarding BDD suite.

Same idiom as the other otto suites: ``ctx`` is the one mutable dict the
Given/When/Then steps share within a scenario. Every scenario runs in
test mode against a cleared in-memory obs store, with the key, manifest
and output directories all pointed at the scenario's own tmp_path so no
scenario ever touches the developer's home directory or another
scenario's artifacts.
"""

from __future__ import annotations

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from otto.obs.config import ENDPOINT_ENV, MODE_ENV, MODE_TEST
from otto.obs.export import obs_test_store


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "onboard: W4 estate onboarding lane")


@pytest.fixture
def ctx() -> dict:
    return {}


@pytest.fixture(autouse=True)
def onboarding_env(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """Test mode, scenario-local key/manifest/output dirs, a clean store."""
    monkeypatch.setenv(MODE_ENV, MODE_TEST)
    monkeypatch.delenv(ENDPOINT_ENV, raising=False)
    monkeypatch.setenv("OTTO_INVENTORY_KEY_PATH", str(tmp_path / "onboard-key.pem"))
    monkeypatch.setenv("OTTO_ONBOARD_DIR", str(tmp_path / "onboard"))
    manifest_dir = tmp_path / "manifests"
    manifest_dir.mkdir()
    monkeypatch.setenv("OTTO_ONBOARD_MANIFEST_DIR", str(manifest_dir))
    _fresh_store()
    yield
    _fresh_store()


def _fresh_store() -> None:
    """Clear the shared store AND replace its span exporter.

    An earlier suite in the same process may have called
    ``handle.shutdown()`` (the integration smoke test does, for six
    handles), and a stopped ``InMemorySpanExporter`` refuses every later
    export while ``clear()`` does not revive it — this scenario's spans
    would silently never land and the coverage gate would read a false
    red. A fresh exporter per scenario removes the leak in both
    directions."""
    store = obs_test_store()
    store.clear()
    store.span_exporter = InMemorySpanExporter()


@pytest.fixture
def manifest_dir(tmp_path):
    return tmp_path / "manifests"


class BlindBackend:
    """A trace backend that can see nothing — the coverage gate's red case."""

    def span_count(self, component: str, window_seconds: float) -> int:
        return 0
