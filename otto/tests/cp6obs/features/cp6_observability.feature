@cp6obs
Feature: Day-0 observability - no black box, no dark boot, no silent drop
  Founder word 2026-08-31: monitoring, logging, tracing, full setup at
  day 0, no gaps; "we don't like any black box". One task is one ULID is
  one trace across every component; a component that cannot emit refuses
  to start; an export failure is loud, never a quiet drop; the coverage
  gate goes red the moment any component is absent from the backend.

  Scenario: Happy path - a two-component flow shares one ULID-linked trace
    Given a spine component and a gateway component instrumented through otto.obs
    When the spine starts a task, logs, and hands the gateway an envelope
    And the gateway continues the task from the envelope with its own span and log line
    Then both components' spans carry the same task ULID
    And both spans share one trace whose id is the ULID's own 128 bits
    And the gateway span is a child of the spine span
    And every log line from both components carries the task ULID

  Scenario: Metrics carry the task ULID and config-driven names
    Given a component instrumented with a metric name overridden in config
    When it records cost, verdict, budget, taint and latency for one task
    Then every metric point carries the task ULID and the component name
    And the cost metric appears under the configured name, not the default

  Scenario: Edge - a missing exporter endpoint refuses boot, never runs dark
    Given no exporter endpoint is configured and test mode is not named
    When a component tries to instrument itself
    Then boot is refused with a structured error naming the missing endpoint
    And no observability handle exists, so the component cannot run dark

  Scenario: Network - an exporter unreachable mid-run is buffered and flagged, never silently dropped
    Given an instrumented component whose exporter starts healthy
    When the component finishes a span while the exporter is up
    And the exporter becomes unreachable and the component finishes another span
    Then the handle reports an unhealthy state
    And the failure was flagged loudly on the alert stream
    And the failed span is buffered, not dropped
    When the exporter comes back and the component flushes
    Then the buffered span reaches the backend and health recovers

  Scenario: Coverage gate - one absent component turns the gate red
    Given the backend holds recent spans from the spine and gateway components only
    When the coverage gate checks spine, gateway and verify against the backend
    Then the verify component is reported ABSENT
    And the gate exits nonzero

  Scenario: Coverage gate - every component present is the only green
    Given the backend holds recent spans from the spine and gateway components only
    When the coverage gate checks spine and gateway against the backend
    Then every checked component is reported PRESENT
    And the gate exits zero
