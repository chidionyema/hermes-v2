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
passed, failed, blocked = [], [], []
for row in rows:
    if only and only not in (row["phase"], row["section"], row["id"]):
        continue
    try:
        rc = subprocess.run(["bash", "-c", row["acceptance_cmd"]],
                            capture_output=True, timeout=25).returncode
    except subprocess.TimeoutExpired:
        rc = 124
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
