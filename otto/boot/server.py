"""The socket. Everything above this module is a pure or nearly-pure
function; this is the one place ``otto.boot`` actually binds a port,
using stdlib ``http.server.ThreadingHTTPServer`` (see the package
docstring for why no web framework was added for two routes).

Routes:

* ``GET /healthz`` — 200, plain text, no auth. A Kubernetes
  liveness/readiness probe target.
* ``POST /telegram-webhook`` — the webhook Telegram calls. Delegates
  entirely to ``otto.boot.app.handle_webhook_body``; this class holds no
  business logic of its own, only the socket plumbing.
* anything else — 404.
"""

from __future__ import annotations

from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from otto.boot.app import handle_webhook_body
from otto.boot.pipeline import ObsHandles
from otto.boot.transport import TelegramTransport
from otto.gateway.core import ToolGateway
from otto.surface.bindings.telegram import TelegramBinding

HEALTHZ_PATH = "/healthz"
WEBHOOK_PATH = "/telegram-webhook"
_MAX_BODY_BYTES = 1_000_000  # a Telegram update is a few KB; refuse anything absurd


@dataclass(frozen=True)
class ServerDeps:
    """Everything the request handler needs, built once at startup and
    shared by every request (no per-request instrumentation boot,
    LAW 50's "nothing boots dark" is satisfied once, at process start)."""

    binding: TelegramBinding
    gateway: ToolGateway
    obs: ObsHandles
    transport: TelegramTransport


def make_handler(deps: ServerDeps) -> type[BaseHTTPRequestHandler]:
    """A ``BaseHTTPRequestHandler`` subclass closed over ``deps`` — the
    stdlib server wants a class, not an instance, so the dependencies
    are bound via closure rather than a constructor the server calls."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "otto-boot/1.0"

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            # Structured JSON logging already happens through
            # ``ObsHandle``; the stdlib default writes unstructured text
            # straight to stderr, which would be a second, inconsistent
            # log format for the same events.
            pass

        def do_GET(self) -> None:  # noqa: N802 - stdlib's naming convention
            if self.path == HEALTHZ_PATH:
                self._respond(200, b"ok")
                return
            self._respond(404, b"not found")

        def do_POST(self) -> None:  # noqa: N802 - stdlib's naming convention
            if self.path != WEBHOOK_PATH:
                self._respond(404, b"not found")
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > _MAX_BODY_BYTES:
                self._respond(400, b'{"ok":false,"error":"bad content-length"}')
                return
            raw_body = self.rfile.read(length)
            result = handle_webhook_body(
                raw_body,
                binding=deps.binding,
                gateway=deps.gateway,
                obs=deps.obs,
                transport=deps.transport,
            )
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
    bind: str = "0.0.0.0",  # noqa: S104 - container bind, by design (task contract)
) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((bind, port), make_handler(deps))
