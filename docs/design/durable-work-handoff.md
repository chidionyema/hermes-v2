# Durable work across a crash — design before code (crew#736 gap rows 8–9)

**Status: design only. No code ships from this document until the founder's word (FIRST LAW:
never write code unless absolutely necessary; the decision this document asks for is named at
the end).**

## The two gaps, restated
- Row 8: a task in flight when the gateway pod dies is gone — nothing records that it was owed.
- Row 9: the 5Gi volume is single-writer (RWO), so the deployment is one replica with Recreate;
  every image roll is a hard stop of whatever was running.

## What already exists (LAW 43 — name the mature owner before building)
The pinned hermes-agent fork already carries a **delivery-obligation ledger** inside `state.db`:
an obligation row is written when Otto owes a reply, and boot replays unmet obligations. The
Recreate strategy plus the entrypoint copy-over-volume invariant means every crash is followed by
exactly one clean boot over the same `state.db`. The estate scheduler owns retries of scheduled
work; LiteLLM owns model-call retries (`config.yaml:9-30`).

## The design: extend the ledger's reach, add no store
1. **Intent before execution.** The gateway writes an obligation row *before* dispatching any
   long-running tool task (today the row is written for replies, not tool work). The row carries
   the chat id, the request text, and a monotonic attempt count.
2. **Boot replay is the recovery.** On boot the existing replay path finds unmet rows and, per
   row: attempts under the ceiling → re-dispatch; at the ceiling → tell the requester it was
   dropped and why (circuit-breaker rule, crew#678 — bounded attempts, loud on open, never a
   silent retry loop).
3. **RWO stays.** One writer over SQLite is a feature, not the defect; the defect was the
   unrecorded intent. No second replica, no RWX migration, no queue product: the buyer-facing
   story is "the ledger is the queue, SQLite is the store, boot is the worker."

## What this rejects, and why
- A message broker (Redis/NATS/Temporal queue): a second platform layer for a single-pod agent;
  the headline deletes second copies. Temporal stays the enterprise lane per the standing ruling.
- RWX/multi-replica: trades a proven single-writer SQLite for distributed-lock risk with zero
  measured demand.

## Proof obligations (when built)
- A drill kills the pod mid-task and the requester still gets either the result or a dropped
  notice within one boot cycle — graded from Telegram delivery, not logs.
- The boot contract (hermes-v2#61) already proves the replay path's process boots; the drill
  proves the obligation survives it.

## The decision asked for
CONFIRM to implement step 1 and 2 as one small change inside the fork's existing obligation
module (estimate: one PR), or hold at design.
