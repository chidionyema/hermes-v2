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

  Scenario: Policy defect - the judgment lane set to the bulk lane's exact model is refused
    Given the environment sets the judgment lane model to minimax
    When the router config is validated
    Then the config is refused because judgment and bulk derive to one model family

  Scenario: Policy defect - the judgment lane on a different model of the bulk lane's family is refused
    Given the environment sets the judgment lane model to another minimax-family model
    When the router config is validated
    Then the config is refused because judgment and bulk derive to one model family

  Scenario: Policy defect - a lane on a model in no family mapping is refused, never defaulted
    Given the environment sets the judgment lane model to a model absent from the family mapping
    When the router config is validated
    Then the config is refused because the model derives to no family

  Scenario: Policy defect - a policy document redefining a shipped model_families entry is refused
    Given a policy document that redefines minimax/minimax-01 to family not-minimax
    When the router config is built from that policy document
    Then the config is refused because a shipped model_families entry was redefined
    And the refusal names minimax/minimax-01, minimax and not-minimax

  Scenario: A policy document adding a brand-new model family is accepted and the guard evaluates it
    Given a policy document that adds a brand-new model mapped to a brand-new family
    And the policy routes the judgment lane to that brand-new model
    When the router config is built from that policy document
    Then the config is accepted
    And the judgment lane's family derives to the brand-new family
