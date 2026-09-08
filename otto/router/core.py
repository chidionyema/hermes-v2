"""The router itself: lane selection, budget guards, bounded retries, fail-closed.

Every path out of ``execute`` is a named ``OutcomeState`` — there is no
silent degradation, no fallback provider, no unbounded retry, and no path
that mints a verified response (P1). A task the router cannot serve is
QUEUED (budget) or NEEDS_HUMAN (network / malformed output), and a person
is notified either way.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

from otto.router.budget import BudgetLedger
from otto.router.config import RouterConfig
from otto.router.contract import (
    MalformedProviderOutput,
    RouterResponse,
    normalise_provider_output,
)
from otto.router.providers import (
    EgressDenied,
    ProviderClient,
    ProviderHTTPError,
    ProviderTimeout,
)
from otto.router.ulid import new_ulid


class OutcomeState(str, Enum):
    """First-class outcomes. COMPLETED is deliberately absent: the router
    can finish a call, but only the Verification Plane completes a task."""

    COMPLETED_UNVERIFIED = "completed_unverified"
    QUEUED_BUDGET = "queued_budget"
    PAUSED_TASK_BUDGET = "paused_task_budget"
    NEEDS_HUMAN = "needs_human"
    REFUSED_MALFORMED = "refused_malformed"


class Notifier(Protocol):
    """Where 'and Chidi is notified' lands. Pluggable; tests use memory,
    the deployment wires Telegram."""

    def notify(self, message: str) -> None: ...


@dataclass
class InMemoryNotifier:
    messages: list[str] = field(default_factory=list)

    def notify(self, message: str) -> None:
        self.messages.append(message)


@dataclass(frozen=True)
class RouterTask:
    """The routing-relevant slice of the task envelope (spec section 3)."""

    input: str
    source: str = "api"
    task_class: str = "research"
    complexity: str = "normal"
    task_id: str = field(default_factory=new_ulid)


@dataclass(frozen=True)
class RouterOutcome:
    """What happened, in full: state, lane, response (when one exists),
    the attempts made, and the money charged. Nothing is implied."""

    state: OutcomeState
    lane: str
    task_id: str
    reason: str = ""
    response: RouterResponse | None = None
    attempts: int = 0
    charged_usd: float = 0.0
    models_called: tuple[str, ...] = ()

    @property
    def executed(self) -> bool:
        return self.response is not None


@dataclass
class Router:
    """Routes tasks to lanes and executes them under policy."""

    config: RouterConfig
    ledger: BudgetLedger
    notifier: Notifier

    # -- lane selection ------------------------------------------------------

    def route(self, task: RouterTask) -> str:
        """Pick the lane from the policy's route table; default lane last.
        Routing never consults the ledger — budget is a guard at execution,
        not a reason to sneak a task onto a cheaper lane."""
        attributes = {
            "source": task.source,
            "class": task.task_class,
            "complexity": task.complexity,
        }
        for match, lane in self.config.routes:
            if all(attributes.get(k) == v for k, v in match.items()):
                return lane
        return self.config.default_lane

    # -- execution under policy ---------------------------------------------

    def execute(self, task: RouterTask, client: ProviderClient) -> RouterOutcome:
        lane = self.route(task)
        lane_cfg = self.config.lanes[lane]

        if self.ledger.exhausted(lane):
            # queue_and_notify: never executed, never re-routed, never a
            # cheaper model. The queue is the outcome.
            self.notifier.notify(
                f"budget exhausted on lane '{lane}': task {task.task_id} queued, "
                f"not executed (policy {self.config.on_budget_exhausted})"
            )
            return RouterOutcome(
                state=OutcomeState.QUEUED_BUDGET,
                lane=lane,
                task_id=task.task_id,
                reason=f"daily budget spent ({self.ledger.spent(lane):.2f} USD)",
            )

        attempts = 0
        charged = 0.0
        models_called: list[str] = []
        timeout_retries = self.config.retry.max_retries_timeout
        http_retries = self.config.retry.max_retries_5xx

        while True:
            attempts += 1
            models_called.append(lane_cfg.model)
            try:
                result = client.complete(
                    lane_cfg.model, task.input, self.config.retry.timeout_seconds
                )
            except ProviderTimeout:
                # The founder's word: a timeout is budget-charged — the
                # provider did work and the bandwidth is gone.
                self.ledger.charge(lane, self.config.retry.timeout_charge_usd)
                charged += self.config.retry.timeout_charge_usd
                if attempts <= timeout_retries:
                    continue
                return self._pause(
                    task,
                    lane,
                    "provider timeout after retries",
                    attempts,
                    charged,
                    models_called,
                )
            except ProviderHTTPError as exc:
                if attempts <= http_retries:
                    continue
                return self._pause(
                    task,
                    lane,
                    f"provider 5xx after retries (last: {exc})",
                    attempts,
                    charged,
                    models_called,
                )
            except EgressDenied as exc:
                # Fail closed at once: egress policy is deliberate; retrying
                # or switching provider would be routing around a control.
                return self._pause(
                    task,
                    lane,
                    f"egress denied: {exc}",
                    attempts,
                    charged,
                    models_called,
                )

            cost = result.tokens / 1000.0 * lane_cfg.cost_per_1k_tokens_usd
            self.ledger.charge(lane, cost)
            charged += cost

            try:
                response = normalise_provider_output(
                    result.text,
                    lane=lane,
                    model=lane_cfg.model,
                    task_id=task.task_id,
                    cost_usd=cost,
                    tokens=result.tokens,
                )
            except MalformedProviderOutput as exc:
                # Refused, never coerced. No RouterResponse exists for this
                # output; a human decides, the spend stays on the ledger.
                self.notifier.notify(
                    f"malformed provider output on lane '{lane}', "
                    f"task {task.task_id}: {exc}"
                )
                return RouterOutcome(
                    state=OutcomeState.REFUSED_MALFORMED,
                    lane=lane,
                    task_id=task.task_id,
                    reason=str(exc),
                    attempts=attempts,
                    charged_usd=charged,
                    models_called=tuple(models_called),
                )

            if self.ledger.over_task_cap(lane, cost):
                self.notifier.notify(
                    f"task {task.task_id} overran lane '{lane}' per-task cap "
                    f"({cost:.4f} USD > {lane_cfg.max_cost_per_task_usd} USD); paused"
                )
                return RouterOutcome(
                    state=OutcomeState.PAUSED_TASK_BUDGET,
                    lane=lane,
                    task_id=task.task_id,
                    reason="per-task cost cap exceeded",
                    response=response,
                    attempts=attempts,
                    charged_usd=charged,
                    models_called=tuple(models_called),
                )

            return RouterOutcome(
                state=OutcomeState.COMPLETED_UNVERIFIED,
                lane=lane,
                task_id=task.task_id,
                response=response,
                attempts=attempts,
                charged_usd=charged,
                models_called=tuple(models_called),
            )

    def _pause(
        self,
        task: RouterTask,
        lane: str,
        reason: str,
        attempts: int,
        charged: float,
        models_called: list[str],
    ) -> RouterOutcome:
        self.notifier.notify(
            f"task {task.task_id} on lane '{lane}' routed to needs_human: {reason}"
        )
        return RouterOutcome(
            state=OutcomeState.NEEDS_HUMAN,
            lane=lane,
            task_id=task.task_id,
            reason=reason,
            attempts=attempts,
            charged_usd=charged,
            models_called=tuple(models_called),
        )
