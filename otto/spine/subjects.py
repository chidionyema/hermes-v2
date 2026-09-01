"""Subject taxonomy, spec §4. Every subject this build ever publishes on
matches the wildcard `otto.*.v1.>`: token 0 is always `otto`, token 2 is
always `v1`. That is the isolation boundary between this build and the
currently running Otto (task instruction: "isolation from current Otto's
subjects") — the current Otto's NATS subjects (if any) do not start with
`otto.`, and even if they did, `validate()` below refuses anything that
does not fit this exact shape, so a bug can publish garbage but it cannot
publish onto a subject the running Otto's consumers would ever match.

The idp platform's own subject grammar (`platform/messaging/subject`,
ADR-0012 D1: `{domain}.{kind}.{aggregate}.{action}.{version}`) is not
reused verbatim here — the founder's spec (§4) already names Otto's five
subjects literally, in a shape D1 does not fit (`otto.tool.v1.req.<tool>`
has 4 tokens where D1 wants 5, and `otto.metric.v1.>` is a wildcard, not
one concrete subject). What *is* reused, deliberately, is D1's underlying
idea — a locked, versioned grammar validated in one place, called from
every publisher and the replay CLI — not its literal five-token regex.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

# The one wildcard every subject in this build must satisfy. NATS subject
# wildcards: `*` matches exactly one token, `>` matches one-or-more
# trailing tokens. This is also the literal string a JetStream consumer
# filter uses to subscribe to "everything Otto publishes."
ROOT_WILDCARD = "otto.*.v1.>"

_SHAPE = re.compile(r"^otto\.[a-z_]+\.v1\.[a-z0-9_.\-<>|]+$")


class StreamName(str, Enum):
    OTTO_TASKS = "OTTO_TASKS"
    OTTO_AUDIT = "OTTO_AUDIT"
    OTTO_VERDICTS = "OTTO_VERDICTS"
    OTTO_METRICS = "OTTO_METRICS"


@dataclass(frozen=True)
class StreamSpec:
    name: StreamName
    subjects: tuple[str, ...]
    retention_days: int
    work_queue: bool  # True = WorkQueue (OTTO_TASKS submitted), False = Limits


# Spec §4's table, verbatim: subject -> stream -> retention.
STREAMS: tuple[StreamSpec, ...] = (
    StreamSpec(
        StreamName.OTTO_TASKS, ("otto.task.v1.>",), retention_days=90, work_queue=False
    ),
    StreamSpec(
        StreamName.OTTO_AUDIT,
        ("otto.tool.v1.>", "otto.mem.v1.>"),
        retention_days=180,
        work_queue=False,
    ),
    StreamSpec(
        StreamName.OTTO_VERDICTS,
        ("otto.verdict.v1.>",),
        retention_days=365,
        work_queue=False,
    ),
    StreamSpec(
        StreamName.OTTO_METRICS,
        ("otto.metric.v1.>",),
        retention_days=30,
        work_queue=False,
    ),
)


class TaskState(str, Enum):
    submitted = "submitted"
    planned = "planned"
    executing = "executing"
    awaiting_verdict = "awaiting_verdict"
    completed = "completed"
    failed = "failed"
    needs_human = "needs_human"


def task_subject(state: TaskState) -> str:
    return f"otto.task.v1.{state.value}"


def tool_req_subject(tool: str) -> str:
    return f"otto.tool.v1.req.{tool}"


def tool_res_subject(tool: str) -> str:
    return f"otto.tool.v1.res.{tool}"


def verdict_subject(result: str) -> str:
    if result not in ("pass", "fail"):
        raise ValueError(f"verdict result must be pass|fail, got {result!r}")
    return f"otto.verdict.v1.{result}"


def mem_subject(op: str) -> str:
    if op not in ("write", "read"):
        raise ValueError(f"mem op must be write|read, got {op!r}")
    return f"otto.mem.v1.{op}"


def metric_subject(*parts: str) -> str:
    if not parts:
        raise ValueError("metric_subject needs at least one part")
    return "otto.metric.v1." + ".".join(parts)


def validate(subject: str) -> str:
    """Refuse any subject this build's own code did not construct via one
    of the helpers above. Called at the one point every publish goes
    through (`bus.py::Publisher.publish`), so a typo in a new call site
    fails loudly instead of silently drifting off the taxonomy."""
    if not _SHAPE.match(subject):
        raise ValueError(
            f"subject {subject!r} does not match {ROOT_WILDCARD!r} "
            "(isolation boundary from the running Otto's subjects)"
        )
    return subject


def dedupe_id(task_id: str, seq: int) -> str:
    """`Nats-Msg-Id`, spec §4: `<task_id>:<seq>`. Same shape the idp outbox
    uses for its own dedupe header (`platform/messaging/outbox/outbox.go`
    relies on the caller building this; here it is one function so every
    publisher computes it identically)."""
    return f"{task_id}:{seq}"
