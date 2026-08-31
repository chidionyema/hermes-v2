"""Task ledger and completion gate: the only path to ``completed``.

Constitution P1: ``awaiting_verdict -> completed`` is the sole transition
into ``completed`` and requires a verdict that (a) is signed by a trusted
prover key, (b) references this task's own task_id, (c) carries this
task's single-use nonce, (d) matches the registered claim package's
hash, and (e) is a hard PASS — a soft verdict satisfies P1 only for
T0/T1 tasks. Every refusal is structured data; every failure mode leaves
the task un-completable and visibly so (needs_human), never silently
complete.
"""

from __future__ import annotations

import enum
import secrets
from dataclasses import dataclass
from typing import Callable, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from otto.verify.bus import VerdictBus, verdict_subject
from otto.verify.errors import StoreUnreachable
from otto.verify.model import PASS, SOFT, ClaimEnvelope, Verdict
from otto.verify.store import VerdictStore


class Tier(enum.Enum):
    T0 = "T0"
    T1 = "T1"
    T2 = "T2"
    T3 = "T3"


class TaskState(enum.Enum):
    SUBMITTED = "submitted"
    PLANNED = "planned"
    EXECUTING = "executing"
    AWAITING_VERDICT = "awaiting_verdict"
    COMPLETED = "completed"
    FAILED = "failed"
    NEEDS_HUMAN = "needs_human"


@dataclass
class Task:
    """The slice of the task envelope (spec section 3) this plane needs."""

    task_id: str
    authority_ceiling: Tier
    deadline_s: float
    created_at: float
    state: TaskState = TaskState.SUBMITTED


@dataclass(frozen=True)
class CompletionDecision:
    """Structured outcome of a verdict submission — never an exception."""

    completed: bool
    reason: str
    task_state: TaskState


@dataclass
class _Pending:
    envelope: ClaimEnvelope
    nonce: str
    nonce_consumed: bool = False


class CompletionGate:
    """Holds pending claims and admits (or refuses) completion.

    The gate holds only *public* keys; it can verify verdicts but can
    never mint one — key separation by construction (P1).
    """

    def __init__(
        self,
        trusted_keys: Mapping[str, bytes],
        store: VerdictStore,
        bus: VerdictBus,
        clock: Callable[[], float],
    ) -> None:
        self._trusted_keys = dict(trusted_keys)
        self._store = store
        self._bus = bus
        self._clock = clock
        self._tasks: dict[str, Task] = {}
        self._pending: dict[str, _Pending] = {}

    # -- lifecycle ----------------------------------------------------------

    def await_verdict(self, task: Task, envelope: ClaimEnvelope) -> str:
        """Register the claim package; mint and return the per-task nonce."""
        if envelope.task_id != task.task_id:
            raise ValueError("claim envelope does not reference this task")
        task.state = TaskState.AWAITING_VERDICT
        self._tasks[task.task_id] = task
        nonce = secrets.token_hex(16)
        self._pending[task.task_id] = _Pending(envelope=envelope, nonce=nonce)
        return nonce

    def task_state(self, task_id: str) -> TaskState:
        return self._tasks[task_id].state

    def expire_overdue(self) -> list[str]:
        """Route every deadline-passed awaiting task to needs_human.

        An absent verdict never completes a task and never lets it be
        silently abandoned as complete.
        """
        now = self._clock()
        expired: list[str] = []
        for task in self._tasks.values():
            if (
                task.state is TaskState.AWAITING_VERDICT
                and now - task.created_at > task.deadline_s
            ):
                task.state = TaskState.NEEDS_HUMAN
                expired.append(task.task_id)
        return expired

    # -- the gate itself ----------------------------------------------------

    def submit_verdict(self, task_id: str, verdict: Verdict) -> CompletionDecision:
        """Apply a verdict from the stream to the named pending task."""
        task = self._tasks.get(task_id)
        pending = self._pending.get(task_id)
        if task is None or pending is None:
            return CompletionDecision(False, "UNKNOWN_TASK", TaskState.FAILED)
        if task.state is not TaskState.AWAITING_VERDICT:
            return self._refuse(task, "NOT_AWAITING_VERDICT")

        if verdict.prover_key_id not in self._trusted_keys:
            return self._refuse(task, "FORGED_UNKNOWN_KEY")
        public_key = Ed25519PublicKey.from_public_bytes(
            self._trusted_keys[verdict.prover_key_id]
        )
        try:
            public_key.verify(verdict.sig_bytes(), verdict.signing_payload())
        except InvalidSignature:
            return self._refuse(task, "FORGED_BAD_SIGNATURE")

        if verdict.task_id != task.task_id:
            return self._refuse(task, "REPLAYED_WRONG_TASK")
        if pending.nonce_consumed or verdict.nonce != pending.nonce:
            return self._refuse(task, "REPLAYED_NONCE")
        if verdict.claim_hash != pending.envelope.claim_hash():
            return self._refuse(task, "TAMPERED_CLAIM_HASH")

        if verdict.result != PASS:
            task.state = TaskState.NEEDS_HUMAN
            self._record_and_publish(verdict)
            return CompletionDecision(False, "VERDICT_FAIL", task.state)

        if verdict.hardness == SOFT and task.authority_ceiling in (Tier.T2, Tier.T3):
            return self._refuse(task, "SOFT_VERDICT_INSUFFICIENT")

        try:
            self._store.record(verdict)
        except StoreUnreachable:
            # Fail closed, loudly: no durable record, no completion.
            task.state = TaskState.NEEDS_HUMAN
            return CompletionDecision(False, "STORE_UNREACHABLE", task.state)

        pending.nonce_consumed = True
        task.state = TaskState.COMPLETED
        self._bus.publish(verdict_subject(verdict), verdict.as_payload())
        return CompletionDecision(True, "COMPLETED", task.state)

    # -- helpers ------------------------------------------------------------

    def _refuse(self, task: Task, reason: str) -> CompletionDecision:
        """Refuse without state change: the task stays awaiting_verdict."""
        return CompletionDecision(False, reason, task.state)

    def _record_and_publish(self, verdict: Verdict) -> None:
        try:
            self._store.record(verdict)
        except StoreUnreachable:
            pass  # a fail verdict is already fail; no completion at stake
        self._bus.publish(verdict_subject(verdict), verdict.as_payload())
