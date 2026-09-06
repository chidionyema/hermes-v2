"""Step 7 (hands & senses, presence): the long-answer progress seam.

Spec ``docs/specs/otto-door-hands-and-senses.md`` step 7. A helper that
thinks for thirty seconds and looks dead for the first eight of them is
worse than one that says "reading" once. This module's pure phase
selector and its single-shot reporter are exactly what a running task
calls; they are proven here without a socket (a recording transport) and
without a live model (the phase logic needs no work to run).

The one invariant that matters — *never a stream of messages* — is the
same rule the founder named for the typing indicator, restated for
progress: one edit, then silence until the answer lands.
"""

from __future__ import annotations

import threading

from otto.boot.presence import (
    PROGRESS_AT_SECONDS,
    progress_phase,
    progress_while,
)


class RecordingTransport:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str]] = []
        self.lock = threading.Lock()

    def send_message(self, chat_id: int, text: str) -> None:
        with self.lock:
            self.messages.append((chat_id, text))


def test_a_short_answer_needs_no_progress_report() -> None:
    assert progress_phase(0.0) is None
    assert progress_phase(PROGRESS_AT_SECONDS - 0.01) is None


def test_a_long_answer_names_exactly_one_phase() -> None:
    phase = progress_phase(PROGRESS_AT_SECONDS + 1)
    assert phase in ("reading", "searching", "writing", "still on it")
    assert isinstance(phase, str)


def test_the_phase_advances_but_never_runs_away() -> None:
    first = progress_phase(PROGRESS_AT_SECONDS + 1)
    later = progress_phase(PROGRESS_AT_SECONDS * 4 + 1)
    # A minutes-long task is not reported as retired: the last phase floors.
    assert progress_phase(PROGRESS_AT_SECONDS * 50) == progress_phase(
        PROGRESS_AT_SECONDS * 50 + 1000
    )
    assert first != later or later == "still on it"


def test_no_reply_address_means_no_progress_handle() -> None:
    transport = RecordingTransport()
    with progress_while(transport, None) as report:
        assert report is None
    assert transport.messages == []


def test_the_reporter_edits_in_place_exactly_once() -> None:
    """Whatever the work does with its handle, at most one progress message
    leaves for the whole answer."""
    transport = RecordingTransport()
    with progress_while(transport, 7) as report:
        assert report is not None
        report("reading")
        report("searching")
        report("writing")
    with transport.lock:
        assert transport.messages == [(7, "reading")]


def test_a_failing_progress_report_does_not_cost_the_answer() -> None:
    class Exploding(RecordingTransport):
        def send_message(self, chat_id, text):
            raise RuntimeError("telegram said no")

    transport = Exploding()
    body_finished = False
    with progress_while(transport, 9) as report:
        assert report is not None
        report("still on it")  # must swallow the transport failure
        body_finished = True

    # Control returned to the work below: a bot that cannot report progress
    # still finishes the answer. Nothing escaped the reporter.
    assert body_finished
    assert transport.messages == []


def test_a_new_progress_handle_still_reports_once() -> None:
    transport = RecordingTransport()
    with progress_while(transport, 9) as first:
        first("reading")
    with progress_while(transport, 9) as second:
        second("still on it")
    with transport.lock:
        assert len(transport.messages) == 2
