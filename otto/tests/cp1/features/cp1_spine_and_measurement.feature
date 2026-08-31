@cp1
Feature: Spine and measurement (spec section 17 Phase 0, section 11, section 15)
  Otto's new build publishes every task event to JetStream, ships a replay CLI,
  a baseline eval corpus and runner, and a signed capability inventory before
  anything else is built. Nothing here touches the currently running Otto or
  any Telegram bot/channel it uses (founder: "new build new branch, new
  telegran channel, new otto, leave current as is").

  Background:
    Given the staging cluster only, zero production credentials in scope
    And the JetStream streams OTTO_TASKS, OTTO_AUDIT, OTTO_VERDICTS, OTTO_METRICS exist

  Scenario: A completed task replays end to end from streams alone
    Given a task has run to completion on the new Otto build
    When an engineer runs "otto replay <task_id>"
    Then the command exits 0
    And the reconstructed task envelope, tool calls and verdict match the original with zero diff
    And no data outside JetStream was read to produce the replay

  Scenario: The eval runner records a baseline against the synthetic corpus
    Given the 40 to 60 task synthetic eval corpus standing in for real Otto and Telegram history (extraction is CP0's harness job)
    When an engineer runs "otto eval run --suite core"
    Then the command exits 0
    And a row is written to the eval_runs table in Postgres for suite core
    And the report names correctness, groundedness, tool-path validity, latency and cost per task

  Scenario: The capability inventory is generated, signed and diffed, never hand written
    Given the current tool, credential-handle, ServiceAccount, egress-domain and lane-budget config
    When CI runs "otto inventory --verify-signature"
    Then the command exits 0
    And the emitted inventory is a signed artifact distinct from any hand-maintained list
    And a diff against the previous deploy's inventory is attached to the CI run

  Scenario: Edge case - a task with zero tool calls still replays cleanly
    Given a task that reached completed using only model judgment, no tool calls
    When an engineer runs "otto replay <task_id>"
    Then the command exits 0
    And the replay shows an empty tool-call list, not an error

  Scenario: Network failure - NATS partitions mid publish, the outbox relay recovers it
    Given a task is submitted through the Postgres transactional outbox
    And NATS JetStream is partitioned before the relay publishes the submission event
    When the partition heals
    Then the outbox relay publishes the pending event with its Nats-Msg-Id intact
    And "otto replay <task_id>" shows no missing sequence number for that task

  Scenario: Bandwidth degradation - a slow JetStream consumer does not lose events
    Given a consumer of OTTO_AUDIT is throttled to a fraction of normal throughput
    When 500 tool req/res events are published during the throttle window
    Then every event is eventually delivered and acknowledged
    And "otto replay <task_id>" for a task spanning the throttle window shows zero gaps
