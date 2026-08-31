"""``instrument(component)`` — the one observability entrypoint (CP6).

Adoption is one line per package::

    obs = instrument("gateway")

The handle owns its own tracer, meter and JSON log emitter (no global
OpenTelemetry state, so components and tests never fight over a global
provider). Every span, log line and metric carries the task ULID; the
ULID's 128 bits ARE the trace id (spec section 3), so one task is one
trace across every component with no id mapping anywhere.

Boot contract: no exporter, no handle. ``instrument()`` raises
``ObsBootError`` when ``OTEL_EXPORTER_OTLP_ENDPOINT`` is absent and the
mode is not the named test escape (``OTTO_OBS_MODE=test``). A component
that cannot emit refuses to start; it never runs dark.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass

from opentelemetry import trace as otel_trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.id_generator import RandomIdGenerator
from opentelemetry.trace import (
    NonRecordingSpan,
    Span,
    SpanContext,
    TraceFlags,
    set_span_in_context,
)

from otto.obs.config import (
    ENDPOINT_ENV,
    ENVELOPE_TRACEPARENT_KEY,
    ENVELOPE_ULID_KEY,
    MODE_ENV,
    MODE_OTLP,
    MODE_TEST,
    ObsConfig,
)
from otto.obs.export import (
    Health,
    MonitoredSpanExporter,
    make_otlp_metric_reader,
    make_otlp_span_exporter,
    obs_test_store,
)
from otto.obs.ulid import is_ulid, new_ulid, ulid_to_trace_id

COMPONENT_ATTR = "otto.component"
ULID_ATTR = "otto.task_ulid"

_pending_trace_id: contextvars.ContextVar[int] = contextvars.ContextVar(
    "otto_obs_pending_trace_id", default=0
)


class ObsBootError(RuntimeError):
    """Structured refusal: this component may not start without an exporter."""

    def __init__(self, component: str, reason: str, remedy: str) -> None:
        self.component = component
        self.reason = reason
        self.remedy = remedy
        super().__init__(json.dumps(self.as_dict()))

    def as_dict(self) -> dict[str, str]:
        return {
            "error": "otto.obs.boot_refused",
            "component": self.component,
            "reason": self.reason,
            "remedy": self.remedy,
        }


@dataclass(frozen=True)
class TaskContext:
    """The task's identity as it travels between components.

    ``remote_trace_id``/``remote_span_id`` are set only on a context
    rebuilt from an envelope; they parent the next span so the receiving
    component's spans join the sender's trace.
    """

    task_ulid: str
    remote_trace_id: int = 0
    remote_span_id: int = 0

    @classmethod
    def new(cls) -> TaskContext:
        return cls(task_ulid=new_ulid())

    @classmethod
    def from_envelope(cls, envelope: dict[str, object]) -> TaskContext:
        """Inherit the task identity a peer component put on the wire."""
        ulid = str(envelope.get(ENVELOPE_ULID_KEY, ""))
        if not is_ulid(ulid):
            raise ValueError(f"envelope carries no valid {ENVELOPE_ULID_KEY}: {ulid!r}")
        trace_id = span_id = 0
        traceparent = str(envelope.get(ENVELOPE_TRACEPARENT_KEY, ""))
        parts = traceparent.split("-")
        if len(parts) == 4 and len(parts[1]) == 32 and len(parts[2]) == 16:
            trace_id = int(parts[1], 16)
            span_id = int(parts[2], 16)
        return cls(task_ulid=ulid, remote_trace_id=trace_id, remote_span_id=span_id)


class UlidIdGenerator(RandomIdGenerator):
    """Root spans take the pending ULID-derived trace id; else random."""

    def generate_trace_id(self) -> int:
        return _pending_trace_id.get() or super().generate_trace_id()


class Metrics:
    """The five day-0 instruments, names from config, ULID on every point."""

    def __init__(self, meter, names, component: str) -> None:
        self._component = component
        self._cost = meter.create_counter(names.cost_by_lane, unit="usd")
        self._verdicts = meter.create_counter(names.verdicts)
        self._budget = meter.create_counter(names.budget_consumption, unit="usd")
        self._taint = meter.create_counter(names.taint_hits)
        self._latency = meter.create_histogram(names.task_latency, unit="ms")

    def _attrs(self, ctx: TaskContext, **extra: str) -> dict[str, str]:
        return {ULID_ATTR: ctx.task_ulid, COMPONENT_ATTR: self._component, **extra}

    def cost(self, ctx: TaskContext, lane: str, usd: float) -> None:
        self._cost.add(usd, self._attrs(ctx, lane=lane))

    def verdict(self, ctx: TaskContext, passed: bool) -> None:
        self._verdicts.add(1, self._attrs(ctx, verdict="pass" if passed else "fail"))

    def budget(self, ctx: TaskContext, lane: str, consumed_usd: float) -> None:
        self._budget.add(consumed_usd, self._attrs(ctx, lane=lane))

    def taint_hit(self, ctx: TaskContext, source: str) -> None:
        self._taint.add(1, self._attrs(ctx, source=source))

    def task_latency(self, ctx: TaskContext, milliseconds: float) -> None:
        self._latency.record(milliseconds, self._attrs(ctx))


class ObsHandle:
    """Everything one component needs: spans, JSON logs, metrics, health."""

    def __init__(self, component: str, config: ObsConfig) -> None:
        self.component = component
        self.config = config
        self.health = Health(component=component)
        self._in_test_mode = config.mode == MODE_TEST

        span_exporter = config.span_exporter
        if span_exporter is None:
            if self._in_test_mode:
                span_exporter = obs_test_store().span_exporter
            else:
                span_exporter = make_otlp_span_exporter(config.endpoint)
        self._monitored = MonitoredSpanExporter(
            span_exporter, self.health, config.export_buffer_max
        )
        resource = Resource.create(
            {"service.name": component, COMPONENT_ATTR: component}
        )
        self._tracer_provider = TracerProvider(
            resource=resource, id_generator=UlidIdGenerator()
        )
        self._tracer_provider.add_span_processor(SimpleSpanProcessor(self._monitored))
        self._tracer = self._tracer_provider.get_tracer("otto.obs")

        if self._in_test_mode:
            metric_reader = obs_test_store().metric_reader_for(component)
        else:
            metric_reader = make_otlp_metric_reader(config.endpoint)
        self._meter_provider = MeterProvider(
            resource=resource, metric_readers=[metric_reader]
        )
        self.metrics = Metrics(
            self._meter_provider.get_meter("otto.obs"),
            config.metric_names,
            component,
        )
        self._log_stream = sys.stdout

    # -- tracing ---------------------------------------------------------
    @contextlib.contextmanager
    def task_span(self, ctx: TaskContext, name: str) -> Iterator[Span]:
        """A span stamped with the task ULID, inside the ULID's own trace."""
        parent = None
        token = None
        if ctx.remote_trace_id and ctx.remote_span_id:
            parent = set_span_in_context(
                NonRecordingSpan(
                    SpanContext(
                        trace_id=ctx.remote_trace_id,
                        span_id=ctx.remote_span_id,
                        is_remote=True,
                        trace_flags=TraceFlags(TraceFlags.SAMPLED),
                    )
                )
            )
        else:
            token = _pending_trace_id.set(ulid_to_trace_id(ctx.task_ulid))
        try:
            with self._tracer.start_as_current_span(
                name,
                context=parent,
                attributes={ULID_ATTR: ctx.task_ulid, COMPONENT_ATTR: self.component},
            ) as span:
                yield span
        finally:
            if token is not None:
                _pending_trace_id.reset(token)

    def envelope(self, ctx: TaskContext) -> dict[str, str]:
        """What goes on the wire so the next component inherits the task."""
        payload = {ENVELOPE_ULID_KEY: ctx.task_ulid}
        span_ctx = otel_trace.get_current_span().get_span_context()
        if span_ctx.is_valid:
            payload[ENVELOPE_TRACEPARENT_KEY] = (
                f"00-{span_ctx.trace_id:032x}-{span_ctx.span_id:016x}-"
                f"{int(span_ctx.trace_flags):02x}"
            )
        return payload

    # -- structured JSON logging ----------------------------------------
    def _log(self, level: str, event: str, ctx: TaskContext, **fields: object) -> None:
        span_ctx = otel_trace.get_current_span().get_span_context()
        line: dict[str, object] = {
            "ts": time.time(),
            "level": level,
            "component": self.component,
            "event": event,
            ULID_ATTR: ctx.task_ulid,
        }
        if span_ctx.is_valid:
            line["trace_id"] = f"{span_ctx.trace_id:032x}"
            line["span_id"] = f"{span_ctx.span_id:016x}"
        line.update(fields)
        print(json.dumps(line, default=str), file=self._log_stream, flush=True)
        if self._in_test_mode:
            obs_test_store().log_lines.append(line)

    def info(self, event: str, ctx: TaskContext, **fields: object) -> None:
        self._log("info", event, ctx, **fields)

    def warning(self, event: str, ctx: TaskContext, **fields: object) -> None:
        self._log("warning", event, ctx, **fields)

    def error(self, event: str, ctx: TaskContext, **fields: object) -> None:
        self._log("error", event, ctx, **fields)

    # -- lifecycle -------------------------------------------------------
    def flush(self) -> bool:
        """Retry anything buffered; True when everything has been exported."""
        return self._monitored.flush_buffered()

    def shutdown(self) -> None:
        self._tracer_provider.shutdown()
        self._meter_provider.shutdown()


def instrument(component: str, config: ObsConfig | None = None) -> ObsHandle:
    """The one entrypoint. Fail-closed boot: no exporter, no handle.

    Default mode requires ``OTEL_EXPORTER_OTLP_ENDPOINT`` in the
    environment and refuses to start the component without it — running
    dark is not an option. ``OTTO_OBS_MODE=test`` is the one named escape
    (in-memory exporters, for suites without a collector).
    """
    cfg = config if config is not None else ObsConfig.from_env()
    if cfg.mode not in (MODE_OTLP, MODE_TEST):
        raise ObsBootError(
            component,
            f"unknown {MODE_ENV} value {cfg.mode!r}",
            f"unset {MODE_ENV} or set it to {MODE_TEST!r}",
        )
    if cfg.mode == MODE_OTLP and not cfg.endpoint:
        raise ObsBootError(
            component,
            f"{ENDPOINT_ENV} is not set; this component will not run dark",
            f"set {ENDPOINT_ENV} to the collector endpoint "
            f"(or {MODE_ENV}={MODE_TEST} in a test suite)",
        )
    return ObsHandle(component, cfg)
