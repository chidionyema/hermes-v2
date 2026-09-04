"""What this process answers on a socket, and what it deliberately does not.

One door: a channel is received in ``otto.ingress.gateway`` and nowhere
else. This process holds the model lanes and answers from the bus, so the
only route it serves is its health probe. The assertion below is about
the handler class rather than a live socket, for the same reason the rest
of this suite is: no test here opens a port.
"""

from __future__ import annotations

from otto.boot import server


def test_the_boot_process_serves_health_and_nothing_else() -> None:
    assert server.HEALTHZ_PATH == "/healthz"
    # The name is gone, not merely unused: a constant left behind is how a
    # removed route grows back.
    assert not hasattr(server, "WEBHOOK_PATH")
    assert "handle_webhook_body" not in dir(server)


def test_the_module_no_longer_imports_the_webhook_handler() -> None:
    source = server.__file__
    with open(source, encoding="utf-8") as fh:
        text = fh.read()
    assert "from otto.boot.app import" not in text
