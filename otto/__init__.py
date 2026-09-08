"""Otto Agent Platform v1.0 — new build, isolated from the running Otto.

Everything under this package is new code for the otto/* lane branches
(crew#768). This package exists only under ``otto/``: nothing here imports
from, or is imported by, any existing module in this repository (founder,
2026-08-31: "new build new branch, new otto, leave current as is"). See
``docs/founder/2026-08-31-otto-platform-build-spec-v1.md`` in the crew repo
(spec of record) for the platform architecture.
"""

from __future__ import annotations
