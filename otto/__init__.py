"""Otto Agent Platform v1.0 — new build, isolated from the running Otto.

This top-level package is shared across the CP build lanes for crew#768.
This lane (CP2b) owns only ``otto/surface`` and ``otto/tests/cp2b``. Peer
lanes own ``otto/spine``, ``otto/gateway``, ``otto/verify``,
``otto/memory``, ``otto/router``, ``otto/obs`` and ``otto/evals`` and are
never imported from here (founder, 2026-08-31: "new build new branch, new
otto, leave current as is").
"""

from __future__ import annotations
