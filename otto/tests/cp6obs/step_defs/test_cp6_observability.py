"""Step definitions for ``features/cp6_observability.feature``.

The founder's day-0 word, each clause a scenario: one ULID links every
component's spans and logs (no black box), a missing endpoint refuses
boot (no dark start), an unreachable exporter is buffered and flagged
(no silent drop), and one absent component turns the coverage gate red
(no gaps).
"""

from __future__ import annotations

import dataclasses
import io
import json
import os

import pytest
from pytest_bdd import given, scenarios, then, when

from otto.obs import ObsBootError, ObsConfig, TaskContext, coverage, instrument
from otto.obs.config import ENDPOINT_ENV
from otto.obs.core import COMPONENT_ATTR, ULID_ATTR
from otto.obs.export import obs_test_store
from otto.obs.ulid import ulid_to_trace_id
from otto.tests.cp6obs.conftest import FlakyExporter, InMemoryBackend, metric_points

scenarios("../features/cp6_observability.feature")


def _spans_by_component(component: str) -> list:
    return [
        span
        for span in obs_test_store().finished_spans()
        if span.resource.attributes.get(COMPONENT_ATTR) == component
    ]


# -- Scenario: two-component ULID-linked trace ---------------------------


@given("a spine component and a gateway component instrumented through otto.obs")
def two_components(ctx: dict, test_mode: None) -> None:
    ctx["spine"] = instrument("spine")
    ctx["gateway"] = instrument("gateway")


@when("the spine starts a task, logs, and hands the gateway an envelope")
def spine_starts_task(ctx: dict) -> None:
    task = TaskContext.new()
    ctx["task"] = task
    spine = ctx["spine"]
    with spine.task_span(task, "accept-task"):
        spine.info("task.accepted", task, source="telegram")
        ctx["envelope"] = spine.envelope(task)


@when("the gateway continues the task from the envelope with its own span and log line")
def gateway_continues(ctx: dict) -> None:
    inherited = TaskContext.from_envelope(ctx["envelope"])
    ctx["inherited"] = inherited
    gateway = ctx["gateway"]
    with gateway.task_span(inherited, "execute-tool"):
        gateway.info("tool.executed", inherited, tool="estate.read")


@then("both components' spans carry the same task ULID")
def spans_share_ulid(ctx: dict) -> None:
    ulid = ctx["task"].task_ulid
    assert ctx["inherited"].task_ulid == ulid
    (spine_span,) = _spans_by_component("spine")
    (gateway_span,) = _spans_by_component("gateway")
    assert spine_span.attributes[ULID_ATTR] == ulid
    assert gateway_span.attributes[ULID_ATTR] == ulid
    ctx["spine_span"], ctx["gateway_span"] = spine_span, gateway_span


@then("both spans share one trace whose id is the ULID's own 128 bits")
def spans_share_trace(ctx: dict) -> None:
    expected = ulid_to_trace_id(ctx["task"].task_ulid)
    assert ctx["spine_span"].context.trace_id == expected
    assert ctx["gateway_span"].context.trace_id == expected


@then("the gateway span is a child of the spine span")
def gateway_is_child(ctx: dict) -> None:
    parent = ctx["gateway_span"].parent
    assert parent is not None
    assert parent.span_id == ctx["spine_span"].context.span_id


@then("every log line from both components carries the task ULID")
def logs_carry_ulid(ctx: dict) -> None:
    ulid = ctx["task"].task_ulid
    lines = obs_test_store().log_lines
    assert {line["component"] for line in lines} == {"spine", "gateway"}
    assert all(line[ULID_ATTR] == ulid for line in lines)


# -- Scenario: config-driven metric names, ULID on every point -----------


@given("a component instrumented with a metric name overridden in config")
def component_with_metric_override(
    ctx: dict, test_mode: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OTTO_OBS_METRIC_COST_BY_LANE", "otto.custom.cost")
    ctx["obs"] = instrument("router")


@when("it records cost, verdict, budget, taint and latency for one task")
def record_all_metrics(ctx: dict) -> None:
    task = TaskContext.new()
    ctx["task"] = task
    obs = ctx["obs"]
    obs.metrics.cost(task, lane="bulk", usd=0.01)
    obs.metrics.verdict(task, passed=True)
    obs.metrics.budget(task, lane="bulk", consumed_usd=0.25)
    obs.metrics.taint_hit(task, source="web")
    obs.metrics.task_latency(task, milliseconds=12.5)


@then("every metric point carries the task ULID and the component name")
def metric_points_carry_ulid(ctx: dict) -> None:
    points = metric_points(obs_test_store().metric_reader_for("router"))
    assert len(points) == 5
    for _name, attrs in points:
        assert attrs[ULID_ATTR] == ctx["task"].task_ulid
        assert attrs[COMPONENT_ATTR] == "router"
    ctx["points"] = points


@then("the cost metric appears under the configured name, not the default")
def cost_metric_name_is_configured(ctx: dict) -> None:
    names = {name for name, _attrs in ctx["points"]}
    assert "otto.custom.cost" in names
    assert "otto.cost.usd" not in names


# -- Scenario: missing endpoint refuses boot -----------------------------


@given("no exporter endpoint is configured and test mode is not named")
def no_endpoint(ctx: dict) -> None:
    assert ENDPOINT_ENV not in os.environ


@when("a component tries to instrument itself")
def component_boots(ctx: dict) -> None:
    ctx["handle"] = ctx["boot_error"] = None
    try:
        ctx["handle"] = instrument("verify")
    except ObsBootError as exc:
        ctx["boot_error"] = exc


@then("boot is refused with a structured error naming the missing endpoint")
def boot_refused(ctx: dict) -> None:
    error = ctx["boot_error"]
    assert isinstance(error, ObsBootError), "boot was NOT refused: ran dark"
    payload = error.as_dict()
    assert payload["error"] == "otto.obs.boot_refused"
    assert payload["component"] == "verify"
    assert ENDPOINT_ENV in payload["reason"]
    assert ENDPOINT_ENV in payload["remedy"]


@then("no observability handle exists, so the component cannot run dark")
def no_handle(ctx: dict) -> None:
    assert ctx["handle"] is None


# -- Scenario: exporter unreachable mid-run ------------------------------


@given("an instrumented component whose exporter starts healthy")
def flaky_component(ctx: dict, test_mode: None) -> None:
    flaky = FlakyExporter(obs_test_store().span_exporter)
    ctx["flaky"] = flaky
    config = dataclasses.replace(ObsConfig.from_env(), span_exporter=flaky)
    ctx["obs"] = instrument("memory", config)


@when("the component finishes a span while the exporter is up")
def span_while_up(ctx: dict) -> None:
    task = TaskContext.new()
    ctx["task"] = task
    with ctx["obs"].task_span(task, "write-note"):
        pass
    assert len(obs_test_store().finished_spans()) == 1


@when("the exporter becomes unreachable and the component finishes another span")
def span_while_down(ctx: dict, capsys: pytest.CaptureFixture) -> None:
    capsys.readouterr()  # drop anything earlier; the alert must be fresh
    ctx["flaky"].down = True
    with ctx["obs"].task_span(ctx["task"], "write-note-again"):
        pass
    ctx["stderr"] = capsys.readouterr().err


@then("the handle reports an unhealthy state")
def handle_unhealthy(ctx: dict) -> None:
    health = ctx["obs"].health
    assert health.state == "unhealthy"
    assert health.export_failures == 1
    assert "unreachable" in health.last_error


@then("the failure was flagged loudly on the alert stream")
def failure_flagged_loudly(ctx: dict) -> None:
    alert = json.loads(ctx["stderr"].strip().splitlines()[-1])
    assert alert["event"] == "otto.obs.export_failure"
    assert alert["component"] == "memory"
    assert alert["state"] == "unhealthy"


@then("the failed span is buffered, not dropped")
def span_buffered(ctx: dict) -> None:
    assert ctx["obs"].health.buffered_spans == 1
    assert ctx["obs"].health.dropped_spans == 0
    assert len(obs_test_store().finished_spans()) == 1  # not yet delivered


@when("the exporter comes back and the component flushes")
def exporter_recovers(ctx: dict) -> None:
    ctx["flaky"].down = False
    ctx["flushed"] = ctx["obs"].flush()


@then("the buffered span reaches the backend and health recovers")
def buffered_span_delivered(ctx: dict) -> None:
    assert ctx["flushed"] is True
    names = [span.name for span in obs_test_store().finished_spans()]
    assert names == ["write-note", "write-note-again"]
    assert ctx["obs"].health.state == "healthy"


# -- Scenarios: coverage gate --------------------------------------------


@given("the backend holds recent spans from the spine and gateway components only")
def backend_with_two_components(ctx: dict, test_mode: None) -> None:
    for component in ("spine", "gateway"):
        obs = instrument(component)
        with obs.task_span(TaskContext.new(), f"{component}-work"):
            pass
    ctx["backend"] = InMemoryBackend()


def _run_gate(ctx: dict, tmp_path, payload: object) -> None:
    components_file = tmp_path / "components.json"
    components_file.write_text(json.dumps(payload), encoding="utf-8")
    out = io.StringIO()
    ctx["exit_code"] = coverage.main(
        ["--components-file", str(components_file)],
        backend=ctx["backend"],
        stdout=out,
    )
    ctx["report"] = json.loads(out.getvalue())


@when("the coverage gate checks spine, gateway and verify against the backend")
def gate_checks_three(ctx: dict, tmp_path) -> None:
    _run_gate(ctx, tmp_path, ["spine", "gateway", "verify"])


@when("the coverage gate checks spine and gateway against the backend")
def gate_checks_two(ctx: dict, tmp_path) -> None:
    _run_gate(ctx, tmp_path, ["spine", "gateway"])


@then("the verify component is reported ABSENT")
def verify_absent(ctx: dict) -> None:
    rows = {row["component"]: row["status"] for row in ctx["report"]["components"]}
    assert rows == {"spine": "PRESENT", "gateway": "PRESENT", "verify": "ABSENT"}
    assert ctx["report"]["result"] == "red"


@then("the gate exits nonzero")
def gate_red(ctx: dict) -> None:
    assert ctx["exit_code"] != 0


@then("every checked component is reported PRESENT")
def all_present(ctx: dict) -> None:
    statuses = {row["status"] for row in ctx["report"]["components"]}
    assert statuses == {"PRESENT"}
    assert ctx["report"]["result"] == "green"


@then("the gate exits zero")
def gate_green(ctx: dict) -> None:
    assert ctx["exit_code"] == 0


@when("the coverage gate is fed an empty component list")
def gate_empty_list(ctx: dict, tmp_path) -> None:
    _run_gate(ctx, tmp_path, [])


@then("the gate reports red naming the broken inventory feed")
def empty_list_is_red(ctx: dict) -> None:
    assert ctx["report"]["result"] == "red", "SILENT GREEN: empty list passed"
    assert ctx["report"]["reason"] == coverage.EMPTY_COMPONENTS_REASON
    assert ctx["report"]["components"] == []


@when("the coverage gate is fed a components file holding an object instead of a list")
def gate_object_payload(ctx: dict, tmp_path) -> None:
    _run_gate(ctx, tmp_path, {"components": ["spine", "gateway"]})


@then("the gate reports red naming the malformed components file")
def malformed_file_is_red(ctx: dict) -> None:
    assert ctx["report"]["result"] == "red", "SILENT GREEN: malformed file passed"
    assert ctx["report"]["reason"] == coverage.MALFORMED_COMPONENTS_REASON
    assert ctx["report"]["parsed_type"] == "dict"
