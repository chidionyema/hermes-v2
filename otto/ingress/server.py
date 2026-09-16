"""The socket: three routes, for every channel and every customer.

* ``GET /healthz`` — 200, for the platform's startup/liveness probes. No credential.
* ``GET /readyz``  — 200 or 503, based on real dependency checks (readiness.py).
                     200 body lists all checks including degraded-but-non-critical deps.
                     503 body names exactly which critical dep failed, so an operator
                     reading the probe response knows what to fix without opening logs.
* ``POST /webhook/{channel}`` — every inbound event, from every channel,
  for every customer. The channel is the last path segment and is looked
  up in the plugin table; it is never matched against a literal here.
* anything else — 404.

There is deliberately no ``/telegram-webhook``, no ``/slack-webhook`` and
no per-customer path. A per-channel route would mean editing the router
to sell to a customer who uses Teams, and a per-customer path would leak
the customer list to anyone who can guess.

``http.server`` for the same reason the boot lane uses it: three routes do
not justify a web framework, and the gateway's work is in ``gateway.py``,
which this module only feeds.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from otto.ingress.gateway import BAD_REQUEST, MAX_BODY_BYTES, EventGateway
from otto.ingress.readiness import ReadinessChecker

HEALTHZ_PATH = "/healthz"
READYZ_PATH = "/readyz"
WEBHOOK_PREFIX = "/webhook/"


def channel_from_path(path: str) -> str | None:
    """The channel named by a webhook path, or ``None`` when the path is
    not a webhook path at all. Query strings are ignored; a channel name
    is a single path segment, so a nested path is not a channel."""
    path = path.split("?", 1)[0]
    if not path.startswith(WEBHOOK_PREFIX):
        return None
    channel = path[len(WEBHOOK_PREFIX) :].strip("/")
    if not channel or "/" in channel:
        return None
    return channel


@dataclass(frozen=True)
class ServerDeps:
    """Built once at process start and shared by every request: one
    gateway, one readiness checker, no per-request boot."""

    gateway: EventGateway
    checker: ReadinessChecker | None = None


def make_handler(deps: ServerDeps) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "otto-ingress/1.0"

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            # Structured JSON logging happens in the gateway; the stdlib
            # default would write a second, unstructured log format for
            # the same events.
            pass

        def do_GET(self) -> None:  # noqa: N802 - stdlib's naming convention
            path = self.path.split("?", 1)[0]
            if path == HEALTHZ_PATH:
                self._respond(200, b"ok", content_type="text/plain")
                return
            if path == READYZ_PATH:
                self._handle_readyz()
                return
            self._respond(404, b'{"ok":false,"reason":"not found"}')

        def _handle_readyz(self) -> None:
            if deps.checker is None:
                self._respond(200, json.dumps({"ok": True, "checks": []}).encode())
                return
            critical_ok, checks = deps.checker.check()
            body = {
                "ok": critical_ok,
                "degraded": not all(c.ok for c in checks),
                "checks": [
                    {
                        "name": c.name,
                        "ok": c.ok,
                        "critical": c.critical,
                        "latency_ms": round(c.latency_ms, 1),
                        **({"detail": c.detail} if c.detail else {}),
                    }
                    for c in checks
                ],
            }
            status = 200 if critical_ok else 503
            self._respond(status, json.dumps(body).encode())

        def do_POST(self) -> None:  # noqa: N802 - stdlib's naming convention
            channel = channel_from_path(self.path)
            if channel is None:
                self._respond(404, b'{"ok":false,"reason":"not found"}')
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_BODY_BYTES:
                self._respond(
                    BAD_REQUEST, b'{"ok":false,"reason":"bad content-length"}'
                )
                return
            raw_body = self.rfile.read(length)
            result = deps.gateway.handle(channel, dict(self.headers), raw_body)
            self._respond(result.status, result.body)

        def _respond(
            self,
            status: int,
            body: bytes,
            content_type: str = "application/json",
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def build_server(
    deps: ServerDeps,
    port: int,
    bind: str = "0.0.0.0",  # noqa: S104 - container bind, by design
) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((bind, port), make_handler(deps))
