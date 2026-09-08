"""Task-lifecycle publish helpers: the surface a later orchestrator
(CP2's tool gateway, the eventual orchestrator daemon) calls to move a
task through spec §3's state machine and publish tool req/res and a
verdict onto the bus. No checkpoint after this one has to reinvent "how
do I put a state transition on `otto.task.v1.<state>` with the right
`Nats-Msg-Id`" — this is that, in one place.

This checkpoint (CP1) also uses it directly: the BDD suite's "a task has
run to completion" scenarios call `Lifecycle` to produce a real,
bus-resident task history for `otto replay` to reconstruct — the same
call shape a real orchestrator will make, not a test-only shortcut.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from otto.spine.bus import Bus, PublishResult
from otto.spine.envelope import TaskEnvelope
from otto.spine.subjects import (
    TaskState,
    task_subject,
    tool_req_subject,
    tool_res_subject,
    verdict_subject,
)


def _payload(task_id: str, **fields) -> bytes:
    return json.dumps(
        {"task_id": task_id, **fields}, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


@dataclass
class Lifecycle:
    bus: Bus
    task_id: str
    _seq: int = field(default=0, init=False, repr=False)

    def _next(self) -> int:
        self._seq += 1
        return self._seq

    async def submit(self, envelope: TaskEnvelope) -> PublishResult:
        if envelope.task_id != self.task_id:
            raise ValueError(
                f"envelope.task_id {envelope.task_id!r} != lifecycle task_id {self.task_id!r}"
            )
        return await self.bus.publish(
            task_subject(TaskState.submitted),
            envelope.canonical_json(),
            task_id=self.task_id,
            seq=self._next(),
        )

    async def transition(self, state: TaskState, **extra) -> PublishResult:
        payload = _payload(self.task_id, state=state.value, **extra)
        return await self.bus.publish(
            task_subject(state), payload, task_id=self.task_id, seq=self._next()
        )

    async def tool_call(
        self, tool: str, *, args: dict, result: dict
    ) -> tuple[PublishResult, PublishResult]:
        req = await self.bus.publish(
            tool_req_subject(tool),
            _payload(self.task_id, tool=tool, args=args),
            task_id=self.task_id,
            seq=self._next(),
        )
        res = await self.bus.publish(
            tool_res_subject(tool),
            _payload(self.task_id, tool=tool, result=result),
            task_id=self.task_id,
            seq=self._next(),
        )
        return req, res

    async def verdict(self, result: str, *, evidence: dict) -> PublishResult:
        payload = _payload(self.task_id, result=result, evidence=evidence)
        return await self.bus.publish(
            verdict_subject(result), payload, task_id=self.task_id, seq=self._next()
        )

    async def run_to_completion(
        self,
        envelope: TaskEnvelope,
        *,
        tool_calls: list[tuple[str, dict, dict]] = (),
        verdict_result: str = "pass",
        verdict_evidence: dict | None = None,
    ) -> None:
        """One call for the common case: submit, execute (0+ tool calls),
        get a passing verdict, complete. `tool_calls=()` is exactly the
        edge-case scenario — completed using only model judgment."""
        await self.submit(envelope)
        await self.transition(TaskState.planned)
        await self.transition(TaskState.executing)
        for tool, args, result in tool_calls:
            await self.tool_call(tool, args=args, result=result)
        await self.transition(TaskState.awaiting_verdict)
        await self.verdict(verdict_result, evidence=verdict_evidence or {})
        await self.transition(
            TaskState.completed if verdict_result == "pass" else TaskState.failed
        )
