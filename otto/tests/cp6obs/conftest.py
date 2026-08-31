"""Shared fixtures for the CP6 observability BDD suite.

``ctx`` is the one mutable dict Given/When/Then steps pass between each
other within a scenario (same idiom as the CP2/CP5 suites). Every
scenario runs with a clean environment and a cleared in-memory store, so
no scenario ever reads another's spans.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from otto.obs.config import ENDPOINT_ENV, MODE_ENV, MODE_TEST
from otto.obs.core import COMPONENT_ATTR
from otto.obs.export import obs_test_store


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "cp6obs: CP6 day-0 observability")


@pytest.fixture
def ctx() -> dict:
    return {}


@pytest.fixture(autouse=True)
def clean_obs_env(monkeypatch: pytest.MonkeyPatch):
    """No inherited endpoint, no inherited mode, an empty store."""
    monkeypatch.delenv(ENDPOINT_ENV, raising=False)
    monkeypatch.delenv(MODE_ENV, raising=False)
    obs_test_store().clear()
    yield
    obs_test_store().clear()


@pytest.fixture
def test_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """The one named escape: in-memory exporters for collector-less runs."""
    monkeypatch.setenv(MODE_ENV, MODE_TEST)


class FlakyExporter:
    """A span exporter whose network can be cut and restored mid-run."""

    def __init__(self, delegate) -> None:
        self.delegate = delegate
        self.down = False

    def export(self, spans: Sequence):
        if self.down:
            raise ConnectionError("collector unreachable")
        return self.delegate.export(spans)

    def shutdown(self) -> None:
        self.delegate.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


class InMemoryBackend:
    """Binds the coverage ``TraceBackend`` Protocol to the in-memory store.

    SigNoz's query API implements the same Protocol at integration; here
    the recency window is trivially satisfied because the store only ever
    holds this scenario's spans.
    """

    def __init__(self, store=None) -> None:
        self._store = store if store is not None else obs_test_store()

    def span_count(self, component: str, window_seconds: float) -> int:
        return sum(
            1
            for span in self._store.finished_spans()
            if span.resource.attributes.get(COMPONENT_ATTR) == component
        )


def metric_points(reader) -> list[tuple[str, dict]]:
    """Flatten one reader's metrics into (name, attributes) pairs."""
    points: list[tuple[str, dict]] = []
    data = reader.get_metrics_data()
    if data is None:
        return points
    for resource_metrics in data.resource_metrics:
        for scope_metrics in resource_metrics.scope_metrics:
            for metric in scope_metrics.metrics:
                for point in metric.data.data_points:
                    points.append((metric.name, dict(point.attributes)))
    return points
