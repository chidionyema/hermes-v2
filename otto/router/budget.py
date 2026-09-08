"""Budget ledger — exhaustion is a first-class outcome, never a silent overrun.

Per-lane daily spend is tracked here; the router consults the ledger BEFORE
every provider call. When a lane is exhausted the configured policy is
``queue_and_notify`` (spec section 5 guards): the task queues, a person is
notified, and it is never served by a cheaper or different model to dodge
the queue.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from otto.router.config import RouterConfig


@dataclass
class BudgetLedger:
    """Daily per-lane spend. In-memory here; a persistent ledger is a later
    checkpoint satisfying the same interface (charge / spent / exhausted)."""

    config: RouterConfig
    _spent: dict[str, float] = field(default_factory=dict)

    def spent(self, lane: str) -> float:
        return self._spent.get(lane, 0.0)

    def charge(self, lane: str, cost_usd: float) -> None:
        """Record money actually spent. Spend is recorded even when the call
        failed or overran a cap — the money is gone either way, and a ledger
        that forgets failed spend under-reports (silent green)."""
        if cost_usd < 0:
            msg = f"negative charge refused: {cost_usd}"
            raise ValueError(msg)
        self._spent[lane] = self.spent(lane) + cost_usd

    def exhausted(self, lane: str) -> bool:
        """True when the lane's daily budget is fully spent."""
        lane_cfg = self.config.lanes.get(lane)
        if lane_cfg is None:
            # An unknown lane has no budget, so it has no headroom: closed.
            return True
        return self.spent(lane) >= lane_cfg.daily_budget_usd

    def over_task_cap(self, lane: str, cost_usd: float) -> bool:
        """True when one task's cost exceeds the lane's per-task cap."""
        lane_cfg = self.config.lanes.get(lane)
        if lane_cfg is None:
            return True
        return cost_usd > lane_cfg.max_cost_per_task_usd
