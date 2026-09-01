@onboard
Feature: Estate onboarding - one command is the admission ticket, fail closed
  W4 (crew#768): `otto onboard <service>` onboards an estate service onto
  Otto in one command - tools registered at an explicit tier, capability
  inventory Ed25519-signed, budgets allocated, traces stamped, a
  plain-English Backstage entity written - and refuses to finish unless
  the coverage gate can see the service in the trace backend. Not
  onboarded means not admitted; a refusal leaves nothing half-onboarded.

  Scenario: Happy path - one command onboards a service end to end
    Given a valid onboarding manifest for "billing-sync"
    When the operator runs otto onboard for "billing-sync"
    Then the command exits green with a structured outcome
    And every declared tool is registered with the gateway at its explicit tier
    And the signed capability inventory is on disk and verifies against the onboarding key
    And the budget allocations match the manifest
    And a Backstage catalog entity file exists with the plain-English title and description
    And the coverage gate saw the service in the trace backend
    And the service's onboarding span carries the service name and the tier

  Scenario: A manifest naming no tier is refused - never default to privileged
    Given an onboarding manifest for "billing-sync" that names no tier
    When the operator runs otto onboard for "billing-sync"
    Then the command exits red with a structured refusal naming the missing tier
    And no catalog entity file and no signed inventory exist for "billing-sync"

  Scenario: The coverage gate cannot see the service - red, loud, nothing half-onboarded
    Given a valid onboarding manifest for "billing-sync"
    And a trace backend that cannot see any spans
    When the operator runs otto onboard for "billing-sync"
    Then the command exits red with a structured coverage failure
    And no catalog entity file and no signed inventory exist for "billing-sync"

  Scenario: A tampered signed inventory is refused, and the evidence is left in place
    Given a valid onboarding manifest for "billing-sync"
    And the service was onboarded once already
    And the stored signed inventory was tampered with afterwards
    When the operator runs otto onboard for "billing-sync"
    Then the command exits red with a structured refusal naming the bad signature
    And the tampered inventory file is left in place for investigation
