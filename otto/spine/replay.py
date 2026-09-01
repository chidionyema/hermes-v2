"""`otto replay <task_id>` (spec §4: "Replay is a feature ... this is the
debugging story and the audit story"; §17 Phase 0 acceptance: "any task
replayable end-to-end from streams"). Reads OTTO_TASKS, OTTO_AUDIT and
OTTO_VERDICTS with plain ephemeral pull consumers and reconstructs one
task's full history — nothing here ever opens a Postgres connection,
which is the literal proof behind the feature's own line "no data outside
JetStream was read to produce the replay."

Sequencing: every event this build publishes for a task carries
`Nats-Msg-Id = <task_id>:<seq>` (`subjects.dedupe_id`), one monotonic
counter per task shared across every subject it publishes to — state
changes, tool req/res, verdicts, memory writes, metrics all draw from the
same counter. That single sequence space is what lets replay say "no gap"
for the whole task in one check, rather than needing to know which
subject each hypothetical missing event would have been on.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from nats.aio.msg import Msg

from otto.spine.bus import Bus
from otto.spine.subjects import StreamName


def canonical_bytes(payload: dict) -> bytes:
    """Same canonicalisation as `TaskEnvelope.canonical_json` (sorted
    keys, no incidental whitespace) so a hash computed here and a hash
    computed from a freshly-constructed envelope are comparable."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def parse_msg_id(msg: Msg) -> tuple[str, int] | None:
    headers = msg.headers or {}
    mid = headers.get("Nats-Msg-Id")
    if not mid:
        return None
    task_id, sep, seq_str = mid.rpartition(":")
    if not sep:
        return None
    try:
        return task_id, int(seq_str)
    except ValueError:
        return None


@dataclass(frozen=True)
class Event:
    seq: int
    subject: str
    payload: dict


@dataclass
class ReplayResult:
    task_id: str
    task_events: list[Event] = field(default_factory=list)
    tool_events: list[Event] = field(default_factory=list)
    verdict_events: list[Event] = field(default_factory=list)
    envelope: dict | None = None
    envelope_hash: str | None = None
    missing_seqs: list[int] = field(default_factory=list)

    @property
    def tool_calls(self) -> list[Event]:
        """Alias matching the spec's vocabulary ("tool calls") for callers
        that don't care about the req/res split."""
        return self.tool_events

    @property
    def found(self) -> bool:
        return bool(self.task_events or self.tool_events or self.verdict_events)

    def as_dict(self) -> dict:
        """The zero-diff comparison surface: two replays of the same task
        produce identical dicts, and a replay compared against the
        envelope + events the test itself published is a plain `==`."""
        return {
            "task_id": self.task_id,
            "envelope": self.envelope,
            "envelope_hash": self.envelope_hash,
            "task_events": [(e.seq, e.subject, e.payload) for e in self.task_events],
            "tool_events": [(e.seq, e.subject, e.payload) for e in self.tool_events],
            "verdict_events": [
                (e.seq, e.subject, e.payload) for e in self.verdict_events
            ],
            "missing_seqs": self.missing_seqs,
        }


def _find_gaps(seqs: list[int]) -> list[int]:
    if not seqs:
        return []
    present = set(seqs)
    return sorted(set(range(1, max(present) + 1)) - present)


def _keep_for_task(msgs: list[Msg], task_id: str) -> list[Event]:
    out: list[Event] = []
    for m in msgs:
        parsed = parse_msg_id(m)
        if parsed is None or parsed[0] != task_id:
            continue
        _, seq = parsed
        out.append(Event(seq=seq, subject=m.subject, payload=json.loads(m.data)))
    out.sort(key=lambda e: e.seq)
    return out


async def replay(bus: Bus, task_id: str) -> ReplayResult:
    """The one function both the CLI and the BDD suite call. Three
    ephemeral, read-only sweeps — one per stream that can hold this
    task's events — then client-side filter by task_id. Nothing about
    this function touches a database, a filesystem cache or model memory:
    everything it returns came off the bus in this call."""
    task_raw = await bus.read_all(
        stream=StreamName.OTTO_TASKS.value, filter_subject="otto.task.v1.>"
    )
    tool_raw = await bus.read_all(
        stream=StreamName.OTTO_AUDIT.value, filter_subject="otto.tool.v1.>"
    )
    verdict_raw = await bus.read_all(
        stream=StreamName.OTTO_VERDICTS.value, filter_subject="otto.verdict.v1.>"
    )

    task_events = _keep_for_task(task_raw, task_id)
    tool_events = _keep_for_task(tool_raw, task_id)
    verdict_events = _keep_for_task(verdict_raw, task_id)

    envelope: dict | None = None
    envelope_hash: str | None = None
    for e in task_events:
        if e.subject == "otto.task.v1.submitted":
            envelope = e.payload
            envelope_hash = hashlib.sha256(canonical_bytes(envelope)).hexdigest()
            break

    all_seqs = [e.seq for e in (*task_events, *tool_events, *verdict_events)]
    missing = _find_gaps(all_seqs)

    return ReplayResult(
        task_id=task_id,
        task_events=task_events,
        tool_events=tool_events,
        verdict_events=verdict_events,
        envelope=envelope,
        envelope_hash=envelope_hash,
        missing_seqs=missing,
    )


async def replay_cli(task_id: str, *, servers: list[str] | None = None) -> int:
    """`otto replay <task_id>`. Exit 0 on a task that was found (even with
    zero tool calls — the edge-case scenario); exit 1 if nothing on any
    stream carries this task_id, or if a gap was found."""
    bus = await Bus(servers=servers).connect()
    try:
        result = await replay(bus, task_id)
    finally:
        await bus.close()

    if not result.found:
        print(f"otto replay: no events found for task {task_id}", flush=True)
        return 1

    print(f"task_id: {result.task_id}")
    print(f"envelope_hash: {result.envelope_hash}")
    print(f"task_events: {len(result.task_events)}")
    print(
        f"tool_calls: {len(result.tool_events)} (empty is a valid outcome, not an error)"
    )
    print(f"verdicts: {len(result.verdict_events)}")
    if result.missing_seqs:
        print(f"missing_seqs: {result.missing_seqs}")
        return 1
    print("missing_seqs: none")
    return 0
