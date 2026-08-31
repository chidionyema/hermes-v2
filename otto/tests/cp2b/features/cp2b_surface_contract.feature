@cp2b @surface_contract
Feature: CP2b channel-plane adapter contract — surface-agnostic envelope and rendering
  The socket every later surface (web, Slack, email, a voice session, a
  glasses card) plugs into (spec docs/specs/otto-platform-v1/
  SURFACE-CONTRACT-DAY0.md, crew#768). Covers the spec's five acceptance
  bullets: identical envelope from two surfaces, an UNVERIFIED marker
  surviving rendering everywhere, loud capability degradation, ambient
  input never instruction-bearing, and the no-voiceprint rule.

  Scenario: The same content normalizes to the same envelope core on Telegram and HTTP
    Given a Telegram binding with chat id 555 bound to principal "chidi"
    And an HTTP binding with caller id "web-555" bound to principal "chidi"
    When the Telegram binding normalizes a message "deploy the release" from chat 555
    And the HTTP binding normalizes a POST with content "deploy the release" from caller "web-555"
    Then both envelopes carry principal "chidi" and trust class "operator"
    And both envelopes carry the content "deploy the release"
    And both envelopes carry a valid ULID correlation id

  Scenario: An UNVERIFIED claim renders the marker on the Telegram surface
    Given a response with an UNVERIFIED claim "the deploy has finished"
    When the response renders for the "telegram" surface with capabilities "text"
    Then the rendered text contains "UNVERIFIED"

  Scenario: An UNVERIFIED claim renders the marker on the HTTP surface
    Given a response with an UNVERIFIED claim "the deploy has finished"
    When the response renders for the "http" surface with capabilities "text"
    Then the rendered text contains "UNVERIFIED"

  Scenario: A capability the surface lacks degrades loudly, never silently
    Given a response with a voice_out part "here is your briefing"
    When the response renders for the "http" surface with capabilities "text"
    Then the rendered message is marked degraded
    And the rendered text contains "http cannot render voice_out"
    And the rendered text contains "here is your briefing"

  Scenario: A capability the surface declares renders natively, undegraded
    Given a response with a voice_out part "here is your briefing"
    When the response renders for the "voice" surface with capabilities "text,voice_out"
    Then the rendered message is not marked degraded

  Scenario: An ambient-classed envelope is never instruction-bearing
    Given an envelope with trust class "ambient" and content "turn off the alarm"
    Then the envelope is not instruction bearing

  Scenario: An operator-classed envelope is instruction-bearing
    Given an envelope with trust class "operator" and content "turn off the alarm"
    Then the envelope is instruction bearing

  Scenario: A voice-claimed principal is refused by validation
    When a principal source "voice" is validated
    Then validation raises a ValueError mentioning "no-voiceprint"

  Scenario: A bound-account principal source passes validation
    When a principal source "bound_account" is validated
    Then validation raises no error
