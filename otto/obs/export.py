"""Export layer: mode resolution, loud failure handling, in-memory store.

Real mode binds the OTLP/HTTP exporter to the endpoint from
``OTEL_EXPORTER_OTLP_ENDPOINT`` (imported lazily, so tests never touch
it). Test mode binds a process-wide in-memory store that the BDD suite
and the coverage gate read.

``MonitoredSpanExporter`` is the no-silent-drop guarantee: an export
failure buffers the spans (bounded, drop-oldest counted), flips the
handle's health to unhealthy and writes one loud JSON line to stderr.
Buffered spans are retried in front of the next export; nothing is ever
dropped quietly.
"""

from __future__ import annotations

import collections
import json
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field

from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)


@dataclass
class Health:
    """Visible export health for one handle — never a black box."""

    component: str
    export_failures: int = 0
    exports_ok: int = 0
    buffered_spans: int = 0
    dropped_spans: int = 0
    last_error: str = ""

    @property
    def healthy(self) -> bool:
        return self.buffered_spans == 0 and self.dropped_spans == 0

    @property
    def state(self) -> str:
        return "healthy" if self.healthy else "unhealthy"

    def as_dict(self) -> dict[str, object]:
        return {
            "component": self.component,
            "state": self.state,
            "export_failures": self.export_failures,
            "exports_ok": self.exports_ok,
            "buffered_spans": self.buffered_spans,
            "dropped_spans": self.dropped_spans,
            "last_error": self.last_error,
        }


class MonitoredSpanExporter(SpanExporter):
    """Wraps any span exporter; failures are buffered and flagged loudly."""

    def __init__(
        self,
        delegate: SpanExporter,
        health: Health,
        buffer_max: int,
        alert_stream=None,
    ) -> None:
        self._delegate = delegate
        self._health = health
        self._buffer: collections.deque[ReadableSpan] = collections.deque()
        self._buffer_max = buffer_max
        self._alert_stream = alert_stream

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        pending = list(self._buffer) + list(spans)
        self._buffer.clear()
        try:
            result = self._delegate.export(pending)
        except Exception as exc:  # noqa: BLE001 — every failure is flagged, none raised into the caller's task
            return self._buffer_and_alert(pending, repr(exc))
        if result is not SpanExportResult.SUCCESS:
            return self._buffer_and_alert(pending, f"exporter returned {result}")
        self._health.exports_ok += 1
        self._health.buffered_spans = 0
        return SpanExportResult.SUCCESS

    def _buffer_and_alert(
        self, pending: list[ReadableSpan], error: str
    ) -> SpanExportResult:
        overflow = max(0, len(pending) - self._buffer_max)
        self._health.dropped_spans += overflow
        self._buffer.extend(pending[overflow:])
        self._health.export_failures += 1
        self._health.buffered_spans = len(self._buffer)
        self._health.last_error = error
        print(
            json.dumps(
                {
                    "ts": time.time(),
                    "level": "error",
                    "event": "otto.obs.export_failure",
                    **self._health.as_dict(),
                }
            ),
            file=self._alert_stream if self._alert_stream is not None else sys.stderr,
            flush=True,
        )
        return SpanExportResult.FAILURE

    def flush_buffered(self) -> bool:
        """Retry buffered spans now; True when the buffer drained."""
        if not self._buffer:
            return True
        return self.export([]) is SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        self._delegate.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        self.flush_buffered()
        return self._delegate.force_flush(timeout_millis)


@dataclass
class InMemoryObsStore:
    """Process-wide sink for test mode; the coverage Protocol binds to it.

    One shared span exporter (so a multi-component flow lands in one
    queryable place, exactly as SigNoz would see it), one metric reader
    and one log list per component.
    """

    span_exporter: InMemorySpanExporter = field(default_factory=InMemorySpanExporter)
    metric_readers: dict[str, InMemoryMetricReader] = field(default_factory=dict)
    log_lines: list[dict[str, object]] = field(default_factory=list)

    def finished_spans(self) -> tuple[ReadableSpan, ...]:
        return self.span_exporter.get_finished_spans()

    def metric_reader_for(self, component: str) -> InMemoryMetricReader:
        return self.metric_readers.setdefault(component, InMemoryMetricReader())

    def clear(self) -> None:
        self.span_exporter.clear()
        self.metric_readers.clear()
        self.log_lines.clear()


_TEST_STORE = InMemoryObsStore()


def obs_test_store() -> InMemoryObsStore:
    """The shared test-mode store (``OTTO_OBS_MODE=test``)."""
    return _TEST_STORE


def make_otlp_span_exporter(endpoint: str) -> SpanExporter:
    """Bind the OTLP/HTTP span exporter to the env-provided endpoint."""
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
        OTLPSpanExporter,
    )

    return OTLPSpanExporter(endpoint=f"{endpoint.rstrip('/')}/v1/traces")


def make_otlp_metric_reader(endpoint: str):
    """Bind the OTLP/HTTP metric exporter to the env-provided endpoint."""
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
        OTLPMetricExporter,
    )
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

    return PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=f"{endpoint.rstrip('/')}/v1/metrics")
    )
