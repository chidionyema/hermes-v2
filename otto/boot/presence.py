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
from typing import Callable, Iterator

from otto.boot.transport import TelegramTransport

#: Telegram clears a chat action after ~5s. Refreshing at 4.5s leaves the
#: indicator continuously lit without a gap the sender would read as the
#: bot giving up.
_REFRESH_SECONDS = 4.5

#: A short-enough wait that the door answers like the bot is alive (typing
#: indicator) and a long-enough wait that a single ``progress`` report is
#: worth the sender's eye. Spec step 7: past eight seconds the sender is
#: told once what Otto is doing, never in a running commentary.
PROGRESS_AT_SECONDS = 8.0

#: The bounded, ordered phases a long answer may name itself in, oldest to
#: newest. One is chosen per ":class:`progress_phase`" call; the sender is
#: never spammed with the transitions.
PROGRESS_PHASES: tuple[str, ...] = (
    "reading",
    "searching",
    "writing",
    "still on it",
)

ProgressReport = Callable[[str], None]


def progress_phase(elapsed_seconds: float) -> str | None:
    """The single progress phrase for ``elapsed_seconds`` of work, or
    ``None`` while the work is short enough that no report is warranted.

    Spec step 7: a task younger than eight seconds needs no progress edit
    (Telegram's own typing indicator covers it); one past that threshold
    is told a phase, *once*. The phrase advances with time so a genuinely
    long answer does not repeat the same word at the minute mark that it
    said at eight seconds, but it never narrates each step.
    """
    if elapsed_seconds < PROGRESS_AT_SECONDS:
        return None
    # One phase per ~quarter past the threshold; the last phase is on
    # the floor so a task that runs for minutes is not reported as retired.
    index = int((elapsed_seconds - PROGRESS_AT_SECONDS) // PROGRESS_AT_SECONDS)
    return PROGRESS_PHASES[min(index, len(PROGRESS_PHASES) - 1)]


@contextmanager
def progress_while(
    transport: TelegramTransport, chat_id: int | None
) -> Iterator[ProgressReport | None]:
    """A progress handle for one long answer: ``yield`` a single-shot
        reporter (or ``None``) whose first call edits a progress message in
        place and whose later calls are ignored.

        Mirrors :func:`typing_while`'s posture: an unknown chat id is a no-op,
        and a progress block that fails must never cost the sender the answer
    the work below is producing. Exactly one ``send_message`` ever leaves
    here for progress, whatever the yield body does with the reporter.
    """
    if chat_id is None:
        yield None
        return
    lock = threading.Lock()
    sent = threading.Event()

    def _report_once(phrase: str) -> None:
        with lock:
            if sent.is_set():
                return
            sent.set()
        try:
            transport.send_message(chat_id, phrase)
        except Exception:  # noqa: BLE001 - a progress courtesy may never raise
            sent.clear()

    yield _report_once


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
