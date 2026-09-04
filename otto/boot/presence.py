"""The typing indicator, kept alive for as long as a model is thinking.

Telegram's ``sendChatAction`` shows "Otto is typing..." for about five
seconds and then clears it. A reasoning lane runs far longer than that
(``moonshot/kimi-k3`` answered a three-word question in 30.5 seconds when
the estate router was probed from inside the ``litellm`` pod on
2026-09-04), so one call at the start of a request leaves the sender
watching silence for the remaining twenty-five seconds, which reads
exactly like the bot having crashed.

This module is the whole fix: a daemon thread that re-sends the action
every ``_REFRESH_SECONDS`` until the work finishes, used as a context
manager around the blocking call. It is deliberately not part of the
router: the router knows nothing about Telegram, and a surface's
courtesies belong to the surface.

Deliberately a thread and not async: ``otto.boot.server`` is a
``ThreadingHTTPServer`` and the pipeline below it is synchronous
throughout, so a thread is the shape that already exists here. Streaming
the model's own partial output (Server-Sent Events) is the real answer
and is not this -- recorded as the open item on the decision record for
2026-09-04.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator

from otto.boot.transport import TelegramTransport

#: Telegram clears a chat action after ~5s. Refreshing at 4.5s leaves the
#: indicator continuously lit without a gap the sender would read as the
#: bot giving up.
_REFRESH_SECONDS = 4.5


@contextmanager
def typing_while(transport: TelegramTransport, chat_id: int | None) -> Iterator[None]:
    """Show "typing" in ``chat_id`` for the duration of the block.

    ``chat_id`` of ``None`` (an update this lane has no reply address for)
    is a no-op, so a caller never has to branch. Every transport error is
    swallowed: an indicator that fails must not cost the sender the answer
    the block is busy producing.
    """
    if chat_id is None:
        yield
        return

    done = threading.Event()

    def _keepalive() -> None:
        while True:
            try:
                transport.send_chat_action(chat_id, "typing")
            except Exception:  # noqa: BLE001 - a courtesy may never raise
                return
            if done.wait(_REFRESH_SECONDS):
                return

    thread = threading.Thread(target=_keepalive, name="otto-typing", daemon=True)
    thread.start()
    try:
        yield
    finally:
        done.set()
        thread.join(timeout=_REFRESH_SECONDS)
