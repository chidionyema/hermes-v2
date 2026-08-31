"""Otto day-0 observability (CP6, crew#768): logging, tracing, metrics — no black box.

One entrypoint for every otto package::

    from otto.obs import instrument
    obs = instrument("router")

The handle carries structured JSON logging, OpenTelemetry tracing and
metrics, all stamped with the task ULID (spec section 3: the ULID doubles
as the OpenTelemetry trace id). A child context built from an envelope
inherits the ULID, so one task is one trace across every component.

Boot contract (fail closed, LAW 50): ``instrument()`` refuses to return a
handle when no exporter can be configured. The exporter endpoint comes
ONLY from the environment variable ``OTEL_EXPORTER_OTLP_ENDPOINT``
(LAW 46 — no literal hosts anywhere in this package). The one named
escape is ``OTTO_OBS_MODE=test``, which binds in-memory exporters so the
test suite runs without a collector. The default is refuse-to-run-dark.

Dependency note: the OpenTelemetry SDK and its OTLP/HTTP exporter are the
export layer; the SDK is imported directly and the OTLP exporter binds
lazily when a real endpoint is configured, so the integration image only
needs ``opentelemetry-exporter-otlp-proto-http`` when W2 points staging at
the estate collector. Coverage (``otto.obs.coverage``) talks to the trace
backend through a Protocol; SigNoz's query API binds there at integration —
no SigNoz URL appears in this package.

R64 note: this package contains no prompts and performs no model calls;
DSPy does not apply here.
"""

from otto.obs.config import MetricNames, ObsConfig
from otto.obs.core import ObsBootError, ObsHandle, TaskContext, instrument
from otto.obs.coverage import CoverageReport, CoverageRow, TraceBackend, check_coverage
from otto.obs.export import InMemoryObsStore

__all__ = [
    "CoverageReport",
    "CoverageRow",
    "InMemoryObsStore",
    "MetricNames",
    "ObsBootError",
    "ObsConfig",
    "ObsHandle",
    "TaskContext",
    "TraceBackend",
    "check_coverage",
    "instrument",
]
