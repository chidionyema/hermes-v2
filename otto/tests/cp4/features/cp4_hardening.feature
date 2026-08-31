Feature: CP4 context budgets and compaction (crew#768 fix pass)

  Independent verification of CP4 found this named on the board row and
  missing: assembling a context from a fact set must never silently
  exceed its configured budget, and a fact set that is over budget must
  be compacted rather than simply dropped - a compacted fact stays
  reachable, it is never destroyed.

  Scenario: Context assembly fits comfortably inside its budget
    Given three short facts and a context budget large enough for all of them
    When the context is assembled
    Then all three facts are included and the assembly is not truncated

  Scenario: Context assembly stops at budget and says so explicitly
    Given five facts and a context budget that only fits the first two
    When the context is assembled
    Then only the facts that fit are included and truncated is true
    And every fact left out is named in the dropped list

  Scenario: Facts dropped for being over budget are compacted, not destroyed
    Given five facts and a context budget that only fits the first two
    When the context is assembled and the dropped facts are compacted
    Then a summary fact is written for the compacted facts
    And every compacted fact's row still exists, linked to the summary via superseded_by
    And the compaction is audited
    And a fresh search no longer returns the compacted facts directly
