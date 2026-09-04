"""Boot for the standalone answering lane.

Fail-closed and in one order, the same order the door boots in and for
the same reason: a pod that has subscribed is a pod that can actually
answer. The collector first (LAW 50 — a workload that cannot be seen
does not start), then the binding database, then the bus, then the
subscription.
"""

from __future__ import annotations

import asyncio
import sys

from otto.boot.pipeline import boot_obs_handles, build_registry
from otto.gateway.core import ToolGateway
from otto.ingress.pg_store import PostgresChannelBindingStore, dsn_from_env
from otto.ingress.secrets import EnvSecretResolver
from otto.ingress.worker import Worker
from otto.obs.core import instrument
from otto.spine.bus import Bus

COMPONENT = "otto-worker"


async def _run() -> None:
    obs = instrument(COMPONENT)

    store = PostgresChannelBindingStore(dsn_from_env())
    store.ensure_schema()

    bus = await Bus().connect()
    await bus.ensure_streams()

    worker = Worker(
        bus=bus,
        store=store,
        secrets=EnvSecretResolver(),
        obs=obs,
        lanes=boot_obs_handles(),
        gateway=ToolGateway(registry=build_registry()),
    )
    await worker.subscribe()
    await worker.run_forever()


def main() -> int:
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        return 0
    except Exception as exc:  # noqa: BLE001 - boot refusal, printed then exited
        # stderr and a non-zero exit, not a traceback: the platform reads
        # the exit status and the operator reads the one line.
        print(f"{COMPONENT}: refusing to start: {exc}", file=sys.stderr)
        return 1
    return 0
