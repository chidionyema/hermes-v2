"""``python -m otto.ingress`` — the process the gateway deployment runs.

Boot is fail-closed and in one order, and the order is the point: every
dependency the door needs is proved before the socket opens, so a pod
that is listening is a pod that can actually accept an event. The
alternative — open the port, discover the database is missing on the
first customer's message — turns a deployment defect into a 401 storm
that reads as a credential problem.

The order:

1. the collector, through ``otto.obs.instrument``, which refuses to
   return a handle when ``OTEL_EXPORTER_OTLP_ENDPOINT`` is unset. A
   workload that cannot be seen does not start.
2. the binding database, which is opened and its schema ensured. Every
   replica may run this; the statements are idempotent.
3. the bus, connected and its streams ensured, because a task that
   cannot be published is a customer event dropped after acknowledgement.
4. the answering lane (``otto.ingress.worker``), which subscribes to the
   tasks this door publishes and talks back on the customer's own
   channel. It runs in this process, beside the socket, rather than as a
   second deployment: it needs exactly what the door already holds --
   the binding table, the secret resolver and the bus -- and a separate
   pod would need all three again for no isolation the door does not
   already have.
5. only then the socket.

Configuration is read here and nowhere below: the gateway, the store and
the publisher all take what they need as arguments, so a test builds the
same objects without an environment.
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Mapping

from otto.ingress.gateway import EventGateway
from otto.ingress.pg_store import PostgresChannelBindingStore, dsn_from_env
from otto.ingress.publisher import JetStreamPublisher
from otto.ingress.secrets import EnvSecretResolver
from otto.ingress.server import ServerDeps, build_server
from otto.ingress.worker import start_worker_thread
from otto.obs.core import instrument
from otto.spine.bus import Bus

COMPONENT = "otto-ingress"
PORT_ENV = "OTTO_INGRESS_PORT"
DEFAULT_PORT = 8080


class PortNotUsable(RuntimeError):
    """``OTTO_INGRESS_PORT`` was set to something that is not a port."""


def port_from_env(environ: Mapping[str, str] | None = None) -> int:
    """The port to listen on.

    LAW 46: the deployment names the port; 8080 is only the fallback for
    a bare local run, and matches the Service's target port.
    """
    env = os.environ if environ is None else environ
    raw = (env.get(PORT_ENV) or "").strip()
    if not raw:
        return DEFAULT_PORT
    try:
        port = int(raw)
    except ValueError as exc:
        raise PortNotUsable(f"{PORT_ENV}={raw!r} is not a number") from exc
    if not 1 <= port <= 65535:
        raise PortNotUsable(f"{PORT_ENV}={raw!r} is outside the range of a port")
    return port


def build_deps(
    loop: asyncio.AbstractEventLoop, *, start_answering: bool = True
) -> ServerDeps:
    """Every dependency, proved, in the order failure should surface.

    ``start_answering`` is the one seam a test uses: the answering lane
    opens its own connection to NATS and would otherwise run for real
    inside a test that only wanted to prove the boot order.
    """
    obs = instrument(COMPONENT)

    store = PostgresChannelBindingStore(dsn_from_env())
    store.ensure_schema()

    bus = loop.run_until_complete(Bus().connect())
    loop.run_until_complete(bus.ensure_streams())

    secrets = EnvSecretResolver()

    if start_answering:
        start_worker_thread(store=store, secrets=secrets, obs=obs)

    return ServerDeps(
        gateway=EventGateway(
            store=store,
            secrets=secrets,
            publisher=JetStreamPublisher(bus, loop),
            obs=obs,
        )
    )


def main() -> int:
    loop = asyncio.new_event_loop()
    try:
        deps = build_deps(loop)
        port = port_from_env()
    except Exception as exc:  # noqa: BLE001 - boot refusal, printed then exited
        # stderr and a non-zero exit, not a raised traceback: the platform
        # reads the exit status, and the operator reads the one line.
        print(f"{COMPONENT}: refusing to start: {exc}", file=sys.stderr)
        return 1

    server = build_server(deps, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
