"""The boot lane: the long-running process the platform actually runs.

Every otto/ package built so far (spine, gateway, verify, memory, router,
surface, obs) is a library. Nothing in the repository imported all of
them into one running process and put a socket on the front — surface
bindings are documented as pure functions with no network call by
design (``otto/surface/bindings/telegram.py``), and ``otto.router.core``
says outright: "the deployment wires Telegram." This package is that
deployment.

``python -m otto.boot`` starts an HTTP server exposing:

* ``POST /telegram-webhook`` — a Telegram update comes in, is normalised
  by ``TelegramBinding``, carried across the platform lanes the same
  way ``otto/tests/integration/test_smoke_assembly.py`` proves in one
  process (surface -> spine -> gateway -> router -> memory), and a reply
  goes back to Telegram through ``sendMessage`` when one is warranted.
* ``GET /healthz`` — 200, no auth, for a Kubernetes liveness/readiness
  probe.

``python -m otto.boot --set-webhook <url>`` registers that URL with
Telegram's ``setWebhook`` endpoint and exits; it does not start a server.

Dependency note (LAW 43 — do not reinvent a wheel a mature tool already
turns): this package adds no new third-party dependency. Two routes and
two outbound Telegram API calls do not need a web framework; the
``otto.router.providers.LiteLLMClient`` pattern already in this
repository talks HTTP to an external API with stdlib ``urllib.request``
alone, and this package follows the same pattern for the same reason —
inbound, stdlib's ``http.server.ThreadingHTTPServer`` already receives a
JSON POST and answers it, so introducing Flask/FastAPI/aiohttp here
would add a licence and a supply-chain surface for something the
standard library already does at this scope. Every route handler above
the transport is a mockable, pure-enough function
(``otto.boot.app.handle_webhook_body``), so the test suite never opens a
real socket.

Token handling (LAW 46): the Telegram bot token is read exactly once, at
boot, from the environment variable ``OTTO_TELEGRAM_BOT_TOKEN``. It is
never written to a file, never logged, and never appears in an
exception message — every error path in this package names the env var,
not its value. A missing token is a loud refusal at import/boot time
(``BootRefused``), never a silent idle process.

R64: this package contains no prompt templates and performs no model
call of its own; the router lane it wires normalises structured output,
it does not compose a prompt.
"""

from __future__ import annotations
