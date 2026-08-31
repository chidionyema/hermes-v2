"""CP1 spine: the task envelope, the JetStream bus, the transactional
outbox, `otto replay` and the signed capability inventory (crew#768 CP1,
spec §3, §4, §15; Phase 0 of the delivery plan, §17).

Nothing in this package talks to a model, a tool or a human. It is the
event substrate every later checkpoint (gateway, verification, memory,
router) publishes to and reads from — P4 of the constitution: "if it
isn't on the stream, it didn't happen."
"""

from __future__ import annotations
