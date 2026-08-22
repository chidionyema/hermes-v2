#!/usr/bin/env python3
"""Run every acceptance command in REQUIREMENTS.jsonl and report the score.

A requirement is closed when its command exits 0. Nothing here can be closed by
an agent asserting it is closed -- that is the whole point.
"""
import json, os, subprocess, sys, collections

HOME = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER = os.path.join(HOME, "REQUIREMENTS.jsonl")
args = [a for a in sys.argv[1:] if not a.startswith("-")]
only = args[0] if args else None

rows = [json.loads(l) for l in open(LEDGER) if l.strip()]
passed, failed, blocked, timed_out = [], [], [], []
for row in rows:
    if only and only not in (row["phase"], row["section"], row["id"]):
        continue
    # 25s was too tight: REQ-005 runs `hermes doctor`, which talks to the
    # provider and took 9.1s on a good run. It tipped over 25s sometimes, and a
    # timeout counted as a failure, so two sweeps a minute apart printed 107 and
    # 106. A green that moves on its own is worse than a red.
    try:
        rc = subprocess.run(["bash", "-c", row["acceptance_cmd"]],
                            capture_output=True, timeout=120,
                            env={**os.environ, "HERMES_HOME": HOME}).returncode
    except subprocess.TimeoutExpired:
        rc = 124
        timed_out.append(row["id"])
    if rc == 0:
        row["status"] = "done"
        passed.append(row)
    elif row.get("blocked_reason"):
        # Blocked is not open. It is a decision waiting on the founder, and it
        # is reported separately so it cannot hide inside the open count.
        row["status"] = "blocked"
        blocked.append(row)
    else:
        row["status"] = "open"
        failed.append(row)

with open(LEDGER, "w") as f:
    for row in rows:
        f.write(json.dumps(row) + "\n")

by_phase = collections.Counter(r["phase"] for r in passed)
tot_phase = collections.Counter(r["phase"] for r in (passed + failed + blocked))
print(f"PASS {len(passed)} / {len(passed)+len(failed)+len(blocked)}"
      + (f"   BLOCKED {len(blocked)}" if blocked else ""))
for ph in sorted(tot_phase):
    print(f"  {ph}: {by_phase[ph]}/{tot_phase[ph]}")
if blocked:
    print("\nblocked - waiting on a founder decision, not on work:")
    for r in blocked:
        print(f"  {r['id']} {r['statement']}")
        print(f"      why: {r['blocked_reason']}")
if failed and "-v" in sys.argv:
    print("\nopen:")
    for r in failed:
        print(f"  {r['id']} {r['section']:5s} {r['statement']}")

if timed_out:
    # Say it out loud. A row that ran out of time was not measured, and calling
    # that a failure is the same lie in the other direction.
    print("TIMED OUT (not measured, not failed): " + ", ".join(timed_out))
