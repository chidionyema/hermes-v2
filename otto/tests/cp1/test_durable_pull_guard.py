"""Live regression test for the `Bus.durable_pull` mismatch guard
(crew#768 CP1). Runs against the real `nats-server -js` process from
`conftest.py`, not a fake.

The verifier's re-check on sha 66ebbbc proved the guard was dead code: it
lived inside `except APIError`, but nats-py 2.15.0's `pull_subscribe`
does not raise on an existing durable — it silently binds to it. No test
exercised the actual `pull_subscribe` call path against a real server, so
the dead guard read green. This test creates a real durable with one
`filter_subject`, then calls `durable_pull` again for the same durable
name with a different `filter_subject`, against the same live server —
the exact call sequence the verifier's live probe used.
"""

from __future__ import annotations

import pytest

from otto.spine.bus import Bus
from otto.tests.cp1.conftest import run_async


def test_durable_pull_refuses_a_config_mismatch_on_an_existing_durable(
    bus: Bus,
) -> None:
    run_async(
        bus.durable_pull(
            stream="OTTO_AUDIT",
            durable="cp1-mismatch-guard-test",
            filter_subject="otto.tool.v1.>",
            deliver_all=True,
        )
    )

    with pytest.raises(RuntimeError, match="incompatible config"):
        run_async(
            bus.durable_pull(
                stream="OTTO_AUDIT",
                durable="cp1-mismatch-guard-test",
                filter_subject="otto.task.v1.>",
                deliver_all=False,
            )
        )


def test_durable_pull_reuses_a_matching_existing_durable(bus: Bus) -> None:
    first = run_async(
        bus.durable_pull(
            stream="OTTO_AUDIT",
            durable="cp1-matching-durable-test",
            filter_subject="otto.tool.v1.>",
            deliver_all=True,
        )
    )
    second = run_async(
        bus.durable_pull(
            stream="OTTO_AUDIT",
            durable="cp1-matching-durable-test",
            filter_subject="otto.tool.v1.>",
            deliver_all=True,
        )
    )

    assert first is not None
    assert second is not None
