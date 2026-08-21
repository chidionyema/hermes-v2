#!/usr/bin/env python3
"""Try to get a claim past the evidence gate without evidence.

Every case here is a way an agent could pass the gate while proving nothing.
When a new bypass gets through in real life, it gets a case here first, then
the gate gets fixed. The test is the record of what we have already been
fooled by.

Run: python3 ci/tests/test_evidence_gate_gaming.py
"""
import os
import subprocess
import sys

GATE = os.path.join(os.path.dirname(__file__), "..", "evidence-gate.js")


def gate(body: str, title: str = "fix: a change") -> int:
    env = dict(os.environ, PR_BODY=body, PR_TITLE=title)
    r = subprocess.run(["node", GATE], env=env, capture_output=True, text=True)
    return r.returncode


REAL_OUTPUT = """## Evidence

Claim: the health endpoint returns 200 after the fix.

```
$ curl -s -o /dev/null -w '%{http_code}\\n' https://prospector-engine.fly.dev/health
200
```
"""

MUST_FAIL = {
    "empty body": "",
    "claim with no block": "## Evidence\n\nClaim: verified in production, all green.\n",
    "empty code block": "## Evidence\n\nClaim: tests are passing.\n\n```\n```\n",
    "placeholder block": "## Evidence\n\nClaim: deployed and working.\n\n```\n<paste>\n```\n",
    "block repeats the claim": (
        "## Evidence\n\nClaim: tests are passing\n\n```\ntests are passing\n```\n"
    ),
    "prose dressed as output": (
        "## Evidence\n\nClaim: the fix works.\n\n```\nI ran it and it seemed fine\n```\n"
    ),
    "reassurance block": "## Evidence\n\nClaim: verified.\n\n```\nlgtm\n```\n",
    "no evidence section": (
        "Claim: everything passes now.\n\n```\n$ pytest\n12 passed\n```\n"
    ),
    "one block, many claims": (
        "## Evidence\n\n"
        "Claim: the build passes.\nClaim: the deploy works.\n"
        "Claim: the health check is green.\nClaim: the migration succeeds.\n\n"
        "```\n$ pytest\n12 passed\n```\n"
    ),
    "evidence quoted from someone else": (
        "## Evidence\n\nClaim: it is fixed.\n\n> ```\n> 200\n> ```\n"
    ),
}

MUST_PASS = {
    "real output": REAL_OUTPUT,
    "no claim at all": "Renamed a variable. No behaviour change.\n",
    "test output": (
        "## Evidence\n\nClaim: the failing case now passes.\n\n"
        "```\n$ pytest tests/test_thing.py\n1 passed in 0.4s\n```\n"
    ),
}


def main() -> int:
    bad = []
    for name, body in MUST_FAIL.items():
        code = gate(body)
        ok = code != 0
        print(f"{'ok  ' if ok else 'MISS'}  blocked: {name} (exit {code})")
        if not ok:
            bad.append(f"{name} got through the gate")

    for name, body in MUST_PASS.items():
        code = gate(body, title="chore: a change")
        ok = code == 0
        print(f"{'ok  ' if ok else 'MISS'}  allowed: {name} (exit {code})")
        if not ok:
            bad.append(f"{name} was blocked and should not have been")

    print()
    if bad:
        for b in bad:
            print("FAIL:", b)
        return 1
    print(f"PASS {len(MUST_FAIL)} bypasses blocked, {len(MUST_PASS)} honest bodies allowed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
