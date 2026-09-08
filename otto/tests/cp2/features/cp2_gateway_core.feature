@cp2 @gateway_core
Feature: CP2 tool-gateway core — schema, tier, taint, audit, human gate
  In-process core of the tool gateway (spec sections 6, 9, 10; constitution
  P2, P5, P7). Sandbox execution and the egress proxy are Phase-1 transport
  work and are covered by the crew spec's cp2_tool_gateway_tiers_sandbox
  feature, not this one. This feature is the deterministic enforcement core
  that a transport (JetStream, sandbox Jobs) will wrap.

  Background:
    Given a fresh tool registry with a cap of 12 tools
    And a fresh in-memory audit emitter
    And a gateway built from that registry and that emitter

  Scenario: A schema-valid, in-tier tool call executes and is fully audited
    Given a T1 tool "fs_write" registered with a strict input schema
    And a task envelope with authority_ceiling T1 and no untrusted context
    When it calls "fs_write" with a valid schema payload
    Then the call executes
    And exactly 1 audit event has been emitted
    And the last audit event records tool "fs_write", decision "executed"

  Scenario: A schema-invalid tool call is refused with a structured denial
    Given a T1 tool "fs_write" registered with a strict input schema
    And a task envelope with authority_ceiling T1 and no untrusted context
    When it calls "fs_write" with a payload missing a required field
    Then the call is denied with reason "SCHEMA_INVALID"
    And exactly 1 audit event has been emitted
    And the last audit event records tool "fs_write", decision "denied"

  Scenario: A tool call below the required tier is refused with a structured denial
    Given a T2 tool "calendar_ops" registered with a strict input schema
    And a task envelope with authority_ceiling T1 and no untrusted context
    When it calls "calendar_ops" with a valid schema payload
    Then the call is denied with reason "TIER_INSUFFICIENT"

  Scenario: Mandatory - untrusted taint caps authority at T1 regardless of the claimed ceiling
    Given a T2 tool "calendar_ops" registered with a strict input schema
    And a task envelope with authority_ceiling T3 and untrusted context from web_fetch
    When it calls "calendar_ops" with a valid schema payload
    Then the call is denied with reason "TAINT_CAP"
    And the denial's effective tier is "T1"
    And the denial's requested tier is "T3"

  Scenario: Registering a 13th tool is refused (constitution cap)
    Given a registry already holding 12 registered tools
    When a 13th tool is registered
    Then registration is refused with ToolCapacityExceeded
    And the registry still holds exactly 12 tools

  Scenario: Every tool call emits a structured audit event, including denials
    Given a T1 tool "fs_write" registered with a strict input schema
    And a task envelope with authority_ceiling T0 and no untrusted context
    When it calls "fs_write" with a valid schema payload
    Then the call is denied with reason "TIER_INSUFFICIENT"
    And exactly 1 audit event has been emitted
    And the last audit event records tool "fs_write", decision "denied"

  Scenario: A T3 call without a human approval token is refused (fail closed)
    Given a T3 tool "email_send" registered with a strict input schema
    And a task envelope with authority_ceiling T3 and no untrusted context
    And no human-gate hook is wired
    When it calls "email_send" with a valid schema payload
    Then the call is denied with reason "HUMAN_APPROVAL_REQUIRED"

  Scenario: A T3 call whose human-gate hook declines is refused (fail closed)
    Given a T3 tool "email_send" registered with a strict input schema
    And a task envelope with authority_ceiling T3 and no untrusted context
    And a human-gate hook that always declines
    When it calls "email_send" with a valid schema payload
    Then the call is denied with reason "HUMAN_APPROVAL_REFUSED"

  Scenario: A T3 call with a valid human approval token executes
    Given a T3 tool "email_send" registered with a strict input schema
    And a task envelope with authority_ceiling T3 and no untrusted context
    And a human-gate hook that always approves
    When it calls "email_send" with a valid schema payload
    Then the call executes
