# Evidence gate: proof it refuses

The gate is only worth having if it fails a pull request that claims a
result and shows nothing. Here it is failing ten of them, and letting three
honest ones through.

Command:

```
$ python3 ci/tests/test_evidence_gate_gaming.py
ok    blocked: empty body (exit 1)
ok    blocked: claim with no block (exit 1)
ok    blocked: empty code block (exit 1)
ok    blocked: placeholder block (exit 1)
ok    blocked: block repeats the claim (exit 1)
ok    blocked: prose dressed as output (exit 1)
ok    blocked: reassurance block (exit 1)
ok    blocked: no evidence section (exit 1)
ok    blocked: one block, many claims (exit 1)
ok    blocked: evidence quoted from someone else (exit 1)
ok    allowed: real output (exit 0)
ok    allowed: no claim at all (exit 0)
ok    allowed: test output (exit 0)

PASS 10 bypasses blocked, 3 honest bodies allowed
```

Single evidence-free body, run directly against the gate:

```
$ PR_BODY='## Evidence

Claim: verified in production.' node ci/evidence-gate.js
Claims found: 1
  Claim: verified in production, all green.
Fenced blocks: 0, usable as output: 0

FAIL: this pull request claims a result and shows no command output that supports it; and makes 1 claims backed by 0 output block(s).
Put the command and what it printed in the body, under "## Evidence".
exit 1
```

Measured 2026-08-22 on this machine. Every new bypass that gets through in
real life becomes a case in ci/tests/test_evidence_gate_gaming.py before the
gate is changed.
