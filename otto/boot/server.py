"""The socket. Everything above this module is a pure or nearly-pure
function; this is the one place ``otto.boot`` actually binds a port,
using stdlib ``http.server.ThreadingHTTPServer`` (see the package
docstring for why no web framework was added for two routes).

Routes:

* ``GET /healthz`` — 200, plain text, no auth. A Kubernetes
  liveness/readiness probe target.
* anything else — 404, including every POST.

There was a ``POST /telegram-webhook`` here, and its removal is the point
of this module now. A second door that speaks to a chat platform directly
is exactly the stitching the one-ingress rule forbids: two places where a
customer is recognised, two places where a channel secret lives, two
places to change when a channel changes. Telegram is received in one
place, ``otto.ingress.gateway``, which authenticates against the binding
table and puts a neutral task on the bus; this process answers what lands
there (``otto.worker``). The webhook body handler and its four-case
failure contract are not deleted, only unbound from a socket here — they
belong to the door, and are exercised by the door's own suite.
"""

from __future__ import annotations

from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from otto.boot.pipeline import ObsHandles
from otto.boot.transport import TelegramTransport
from otto.gateway.core import ToolGateway
from otto.surface.bindings.telegram import TelegramBinding

HEALTHZ_PATH = "/healthz"


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
            # This process posts nothing and receives nothing. A POST here
            # is either a stale webhook registration or a probe, and both
            # deserve the same flat answer.
            self._respond(404, b"not found")

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
