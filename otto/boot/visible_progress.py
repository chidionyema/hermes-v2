"""CP2 (crew#892): progress a founder can *see*, not one frozen typing bar.

When the webhook handler answers an allowlisted sender whose words need a
maybe-half-minute reasoning pass, it posts ONE placeholder message to the
sender nearly the moment the instruction is read, and later *edits that same
message* in place as the answer takes shape — "reading the repo…", a fresher
line each few tool calls — so the sender watches Otto work instead of staring
at a single ``…`` that Telegram let him believe was a stuck bot. The final
answer is then edited into that self-same message, so the transcript reads as
one living message, exactly the way a human edits one note as it gets real.

This module is deliberately a thin orchestration on top of the transport
primitives CP2 landed first (``send_message`` now returns the ``message_id``
Telegram assigned; ``edit_message`` posts ``editMessageText``). It has no room
of its own for a transport — its whole job is to be *optional*: a channel or a
message with no place for an in-place edit must behave byte-identically to
before, and a courtesy that could cost a sender their answer is a bug.
"""

from __future__ import annotations

import time

from otto.boot.transport import TelegramTransport

# A turn that edits more often than this is noise, not progress; the pipeline
# calls ``note`` at meaningful transitions and we ratelimit to one edit per
# interval so a fast tool burst cannot spam the Bot API.
_MIN_PROGRESS_SECONDS = 2.0


class ProgressEditor:
    """Own one placeholder message and the edits that keep it alive.

    Usage (see ``otto.boot.app.handle_webhook_body``): when a chat should see
    progress, construct one editor, call ``begin(chat_id, obs)`` to send and
    remember the placeholder, let the pipeline call ``note()`` / ``phase()`` as
    work proceeds, and hand the held ``message_id`` to ``deliver`` so the final
    answer is edited in place. Every call here is a courtesy: an exception
    from the Bot API is reported to ``obs`` (never raised past the caller) and
    the editor quietly carries on or degrades to "a normal fresh message",
    which is the exact pre-CP2 shape.

    Nothing in this class *depends* on a placeholder having been sent. If
    ``begin`` could not reach Telegram, ``active`` stays ``False`` and callers
    take the unchanged path.
    """

    def __init__(self, transport: TelegramTransport) -> None:
        self._transport = transport
        self._chat_id: int | None = None
        self._message_id: int | None = None
        self._last_note_at = 0.0

    @property
    def active(self) -> bool:
        """True when we hold a message_id a later ``edit_message`` can name."""
        return self._message_id is not None

    @property
    def message_id(self) -> int | None:
        return self._message_id

    def begin(self, chat_id: int, text: str, *, obs=None) -> None:
        """Post the one placeholder and remember its id so every later edit
        (and the final in-place deliver) can name it."""
        self._chat_id = chat_id
        try:
            self._message_id = self._transport.send_message(chat_id, text)
        except Exception as exc:  # noqa: BLE001 - courtesy, never costs the answer
            self._message_id = None
            self._report("progress.begin_failed", exc, obs)
        if self._message_id is None or self._message_id < 0:
            # sendMessage succeeded structurally but Telegram gave us no id,
            # or the send raised; either way there is nothing to edit later.
            self._message_id = None
        else:
            if obs is not None:
                obs.boot.info(
                    "progress.begin",
                    _ctx(),
                    chat_id=chat_id,
                    message_id=self._message_id,
                )

    def note(self, text: str, *, obs=None) -> None:
        """Edit the placeholder to a fresher ``text``, ratelimited.

        Called by the pipeline at a lane change and every few tool calls. If
        no placeholder is active this is a no-op — there must never be a
        second message or a stray edit on the plain path.
        """
        if not self.active or self._chat_id is None:
            return
        now = time.monotonic()
        if now - self._last_note_at < _MIN_PROGRESS_SECONDS:
            return
        self._last_note_at = now
        self._replace(text, obs)

    def _replace(self, text: str, obs) -> None:
        try:
            self._transport.edit_message(self._chat_id, self._message_id, text)
        except Exception as exc:  # noqa: BLE001 - courtesy
            self._report("progress.edit_failed", exc, obs)
            return
        if obs is not None:
            obs.boot.info(
                "progress.edited",
                _ctx(),
                chat_id=self._chat_id,
                message_id=self._message_id,
            )

    def _report(self, event: str, exc: BaseException, obs) -> None:
        if obs is not None:
            obs.boot.warning(event, _ctx(), chat_id=self._chat_id, error=str(exc))


def _ctx():
    from otto.obs.core import TaskContext

    return TaskContext.new()
