"""Observability configuration — everything tunable is config, never a constant.

The exporter endpoint comes ONLY from ``OTEL_EXPORTER_OTLP_ENDPOINT``
(LAW 46). Metric names are config-driven: defaults below, each
overridable through ``OTTO_OBS_METRIC_<FIELD>`` or by passing an explicit
``MetricNames`` to ``instrument()``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields

ENDPOINT_ENV = "OTEL_EXPORTER_OTLP_ENDPOINT"
MODE_ENV = "OTTO_OBS_MODE"
MODE_TEST = "test"
MODE_OTLP = "otlp"

# Envelope field names: protocol constants (a wire format, not a location).
ENVELOPE_ULID_KEY = "otto.task_ulid"
ENVELOPE_TRACEPARENT_KEY = "otto.traceparent"


@dataclass(frozen=True)
class MetricNames:
    """Names of the five day-0 metrics; every one overridable by config."""

    cost_by_lane: str = "otto.cost.usd"
    verdicts: str = "otto.verdicts"
    budget_consumption: str = "otto.budget.consumed"
    taint_hits: str = "otto.taint.hits"
    task_latency: str = "otto.task.latency_ms"

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> MetricNames:
        """Defaults, with ``OTTO_OBS_METRIC_<FIELD>`` overrides applied."""
        env = os.environ if environ is None else environ
        overrides = {}
        for spec in fields(cls):
            value = env.get(f"OTTO_OBS_METRIC_{spec.name.upper()}", "")
            if value:
                overrides[spec.name] = value
        return cls(**overrides)


@dataclass(frozen=True)
class ObsConfig:
    """Resolved configuration for one ``instrument()`` call.

    ``mode`` is ``"otlp"`` (default: an endpoint is REQUIRED, boot refuses
    without one) or ``"test"`` (in-memory exporters; the one named escape
    for suites that run without a collector). ``endpoint`` is only ever
    read from the environment — never passed as a literal by callers.

    ``span_exporter`` is the test seam and the integration binding point:
    when set, it replaces the mode-selected span exporter (the network
    BDD scenarios inject a failing exporter here).
    """

    mode: str = MODE_OTLP
    endpoint: str = ""
    metric_names: MetricNames = field(default_factory=MetricNames)
    export_buffer_max: int = 512
    coverage_window_seconds: float = 900.0
    span_exporter: object | None = None

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> ObsConfig:
        env = os.environ if environ is None else environ
        mode = env.get(MODE_ENV, "").strip() or MODE_OTLP
        return cls(
            mode=mode,
            endpoint=env.get(ENDPOINT_ENV, "").strip(),
            metric_names=MetricNames.from_env(env),
        )
