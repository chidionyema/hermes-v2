@cp5
Feature: Router and structured outputs (spec section 17 Phase 4, section 5)
  Three lanes route by policy, every model call returns the universal response
  contract, unverified claims are flagged by the gateway not the model, and a
  budget guard queues and notifies rather than degrading silently.

  Background:
    Given lane budgets judgment 15, bulk 5, verify 3 USD per day
    And on_budget_exhausted is queue_and_notify

  Scenario: Happy path - a router or prompt change records an eval delta
    Given a change to router policy
    When an engineer runs "otto eval diff --baseline <ref>"
    Then a per-lane eval delta is recorded and shown before any merge word is given

  Scenario: A claim with no evidence is flagged in the rendered output
    Given a model response whose claims array includes an entry with empty evidence_refs
    When it is rendered to Telegram
    Then that claim is prefixed "unverified:" by the gateway's rendering rule
    And this is not a model instruction, it is enforced independent of what the model wrote

  Scenario: Ungrounded-claim rate meets the bar
    When an engineer runs "otto eval run --suite core"
    Then the ungrounded-claim rate is below 5 percent

  Scenario: Edge case - a claim's evidence_refs resolve but do not actually support the claim
    Given a claim whose evidence_refs point to a real tool_call_id
    But the referenced tool result does not support the claim's text
    When the groundedness check runs mechanically
    Then the claim is counted as ungrounded, not as verified merely because a ref exists

  Scenario: Mandatory - a lane's daily budget is exhausted and the guard queues, not degrades
    Given the bulk lane has spent its full daily budget of 5 USD
    When a new task routes to the bulk lane
    Then it is queued, not executed
    And Chidi is notified
    And it is never silently served by a cheaper or different model to avoid the queue

  Scenario: Network failure - the judgment lane provider returns errors mid task
    Given a task routed to the judgment lane
    When the provider returns repeated 5xx responses or times out
    Then the router retries once per lane policy
    And after exhausting retries the task is routed to needs_human
    And it never silently falls back to a different, unconfigured provider
