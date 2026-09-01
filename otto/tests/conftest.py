"""Suite-wide test environment (W2 wiring, crew#768).

Every package's entrypoint now boots through ``otto.obs.instrument`` and
refuses to run dark. The suites run without a collector, so the one named
escape — ``OTTO_OBS_MODE=test`` (in-memory exporters) — is defaulted here
for the whole test process and every CLI subprocess it spawns. Only a
default: an explicitly exported mode wins, and the cp6obs suite clears
the variable per-scenario to prove the fail-closed boot contract.
"""

from __future__ import annotations

import os

from otto.obs.config import MODE_ENV, MODE_TEST

os.environ.setdefault(MODE_ENV, MODE_TEST)
