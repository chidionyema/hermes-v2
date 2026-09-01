"""CP1 spine: the task envelope, the JetStream bus, the transactional
outbox, `otto replay` and the signed capability inventory (crew#768 CP1,
spec §3, §4, §15; Phase 0 of the delivery plan, §17).

Nothing in this package talks to a model, a tool or a human. It is the
event substrate every later checkpoint (gateway, verification, memory,
router) publishes to and reads from — P4 of the constitution: "if it
isn't on the stream, it didn't happen."
"""

from __future__ import annotations


def boot(config=None):
    """W2 wiring (crew#768): this package's boot entrypoint.

    Instruments the component through ``otto.obs`` and returns the
    handle, or raises ``ObsBootError`` — nothing boots dark (LAW 50).
    The exporter endpoint comes only from ``OTEL_EXPORTER_OTLP_ENDPOINT``;
    ``OTTO_OBS_MODE=test`` binds in-memory exporters for suites.
    """
    from otto.obs import instrument

    return instrument("spine", config)
