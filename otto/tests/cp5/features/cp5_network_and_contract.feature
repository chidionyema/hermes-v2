@cp5
Feature: Network failure handling and the universal contract
  The founder's explicit word: slow providers, 5xx flaps and denied egress
  are each covered, each fail-closed. Malformed provider output is refused,
  never coerced, and no lane's output is ever self-certified (P1).

  Scenario: Network failure - a slow provider times out, the retry is budget-charged, then the task pauses
    Given a task routed to the bulk lane
    And a provider that never answers inside the timeout
    When the router executes the task
    Then each timed-out attempt is charged to the lane budget
    And the router retried exactly once before pausing
    And the task is routed to needs_human and Chidi is notified

  Scenario: Network failure - egress is denied and the router fails closed at once
    Given a task routed to the bulk lane
    And a provider whose egress is denied by network policy
    When the router executes the task
    Then the task is routed to needs_human after a single attempt
    And no retry and no fallback provider was attempted
    And Chidi is notified

  Scenario: Malformed provider output is refused, never coerced
    Given a task routed to the bulk lane
    And a provider that answers with output missing the claims array
    When the router executes the task
    Then the outcome is refused_malformed and no response object exists
    And the missing field was not silently defaulted

  Scenario: Bulk-lane output is never self-certified
    Given a task routed to the bulk lane
    And a provider that answers with a well-formed contract document
    When the router executes the task
    Then the normalised response carries verification status unverified
    And no field the provider can emit produces a verified response

  Scenario: A single task overruns the lane's per-task cost cap and pauses loudly
    Given a task routed to the bulk lane
    And a provider whose answer costs more than the bulk per-task cap
    When the router executes the task
    Then the outcome is paused_task_budget and Chidi is notified
    And the overrun spend is still recorded on the lane ledger
