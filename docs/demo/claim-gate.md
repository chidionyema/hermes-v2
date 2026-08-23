# Demo: the claim gate

A reply that says DONE without a green verification run behind it is restamped
before it reaches you. Real run, 2026-08-24, against a throwaway ledger:

```
$ python3 -c "... mark_workspace_edited(...); stamp_unproven_done(...) ..."

--- unproven claim in ---
[Architect] DONE: fixed the scheduler, all green.
--- what reaches the founder ---
[Architect] UNVERIFIED: fixed the scheduler, all green.

⚠️ UNVERIFIED: files were edited this session and no verification run has passed since the last edit (proj: unverified).

--- same claim after a real green run ---
[Architect] DONE: fixed the scheduler, all green.
```

What just happened: the session had edited maestro.py and run nothing, so its
DONE became UNVERIFIED with one line saying why. Then a passing verification
run was recorded for the same session, and the identical claim passed through
untouched. The gate never blocks or shortens a reply — it only relabels a
claim the ledger cannot back.

The test suite, same day:

```
$ python3 -m pytest tests/ -q -k claim_gate
..........
10 passed, 17 deselected in 5.22s
```
