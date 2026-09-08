"""The socket: two routes, for every channel and every customer.

* ``GET /healthz`` — 200, for the platform's probes. No credential.
* ``POST /webhook/{channel}`` — every inbound event, from every channel,
  for every customer. The channel is the last path segment and is looked
  up in the plugin table; it is never matched against a literal here.
* anything else — 404.

There is deliberately no ``/telegram-webhook``, no ``/slack-webhook`` and
no per-customer path. A per-channel route would mean editing the router
to sell to a customer who uses Teams, and a per-customer path would leak
the customer list to anyone who can guess.

``http.server`` for the same reason the boot lane uses it: two routes do
not justify a web framework, and the gateway's work is in ``gateway.py``,
which this module only feeds.
"""

from __future__ import annotations

from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from otto.ingress.gateway import BAD_REQUEST, MAX_BODY_BYTES, EventGateway

HEALTHZ_PATH = "/healthz"
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
    gateway, one instrumented handle, no per-request boot."""

    gateway: EventGateway


def make_handler(deps: ServerDeps) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "otto-ingress/1.0"

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            # Structured JSON logging happens in the gateway; the stdlib
            # default would write a second, unstructured log format for
            # the same events.
            pass

        def do_GET(self) -> None:  # noqa: N802 - stdlib's naming convention
            if self.path == HEALTHZ_PATH:
                self._respond(200, b"ok")
                return
            self._respond(404, b'{"ok":false,"reason":"not found"}')

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

        def _respond(self, status: int, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
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
