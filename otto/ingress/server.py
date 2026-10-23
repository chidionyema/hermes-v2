"""The socket: three routes, for every channel and every customer.

* ``GET /healthz`` — 200, for the platform's probes. No credential.
* ``GET /turns/{task_id}`` — the answer to a turn, read back by the caller
  that asked for it. This is what makes a surface that cannot be pushed to
  (``HttpPlugin``) a surface that can be answered at all: the 202 hands out
  the task id, and this route is the fetch. Authenticated by the same
  credential the turn was sent with; the tenant comes off that credential,
  never off the path.
* ``POST /webhook/{channel}`` — every inbound event, from every channel,
  for every customer. The channel is the last path segment and is looked
  up in the plugin table; it is never matched against a literal here.
* anything else — 404.

There is deliberately no ``/telegram-webhook``, no ``/slack-webhook`` and
no per-customer path. A per-channel route would mean editing the router
to sell to a customer who uses Teams, and a per-customer path would leak
the customer list to anyone who can guess.

``http.server`` for the same reason the boot lane uses it: a few routes do
not justify a web framework, and the gateway's work is in ``gateway.py``,
which this module only feeds. What this module does own is the failure of
that work: an exception escaping the gateway used to close the connection
with no line in the log at all (measured 2026-10-23 — four days of accepted
turns dropped and nothing written down), so every request is now answered
and every exception is named before it is answered.
"""

from __future__ import annotations

from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

from otto.ingress.gateway import BAD_REQUEST, MAX_BODY_BYTES, EventGateway
from otto.obs.core import ObsHandle, TaskContext

HEALTHZ_PATH = "/healthz"
WEBHOOK_PREFIX = "/webhook/"
TURNS_PREFIX = "/turns/"

#: An answer that does not exist yet is a 404, not a 500: a caller polls
#: this route after its 202 and the first few reads legitimately miss.
NOT_FOUND = 404
UNAVAILABLE = 503


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
    #: The door's own handle, for the one line the gateway cannot write:
    #: an exception that escaped it. A process that cannot log its failures
    #: is a process whose failures are invisible, which is how an accepted
    #: turn could be dropped for four days without a single log line.
    obs: ObsHandle | None = None


def task_id_from_path(path: str) -> str | None:
    """The task id named by a read-back path, or ``None`` when the path is
    not a read-back path at all. A task id is one path segment."""
    path = path.split("?", 1)[0]
    if not path.startswith(TURNS_PREFIX):
        return None
    task_id = unquote(path[len(TURNS_PREFIX) :]).strip("/")
    if not task_id or "/" in task_id:
        return None
    return task_id


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
            task_id = task_id_from_path(self.path)
            if task_id is not None:
                self._guard(
                    lambda: deps.gateway.answer_for(dict(self.headers), task_id)
                )
                return
            self._respond(404, b'{"ok":false,"reason":"not found"}')

        def do_POST(self) -> None:  # noqa: N802 - stdlib's naming convention
            channel = channel_from_path(self.path)
            if channel is None:
                self._respond(404, b'{"ok":false,"reason":"not found"}')
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                self._respond(
                    BAD_REQUEST, b'{"ok":false,"reason":"bad content-length"}'
                )
                return
            if length <= 0 or length > MAX_BODY_BYTES:
                self._respond(
                    BAD_REQUEST, b'{"ok":false,"reason":"bad content-length"}'
                )
                return
            raw_body = self.rfile.read(length)
            self._guard(
                lambda: deps.gateway.handle(channel, dict(self.headers), raw_body)
            )

        def _guard(self, call):
            """Answer the request, always.

            The gateway already turns every outcome it knows about into a
            status. This catches what is left: the exception that used to
            escape ``do_POST``, close the socket with no status, and leave
            no line behind. It is written down as ``ingress.failed`` with
            the exception's type and a 500, so a caller learns the turn
            failed instead of guessing from a dropped connection.
            """
            try:
                result = call()
            except Exception as exc:  # noqa: BLE001 - the last resort
                if deps.obs is not None:
                    deps.obs.info(
                        "ingress.failed",
                        TaskContext.new(),
                        path=self.path,
                        error=type(exc).__name__,
                        detail=str(exc)[:300],
                    )
                self._respond(
                    500,
                    b'{"ok":false,"reason":"the door failed before it could answer"}',
                )
                return
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
