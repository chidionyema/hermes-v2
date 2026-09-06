"""A verdict for a chat reply, from the verify lane, before it is rendered.

P1 (otto/router/render.py): a claim renders with the unverified marker until a
verdict says otherwise. Until 2026-09-06 nothing on the answering path ever
issued one, so every line Otto sent -- "I am Otto" included -- carried the
marker, and a marker on everything tells the reader nothing (founder,
2026-09-06 03:32: "why the unverified disclaimer").

This is that verdict. One call on the verify lane (a different model on a
different lane from the one that answered: OTTO_ROUTER_LANE_VERIFY_MODEL),
over every statement of the reply at once, answering two structural
questions per statement: is it conversational (a greeting, a question back,
an offer, a description of Otto itself -- nothing checkable about the world),
and if it is a fact, does the context the answering model was given support
it. A conversational line or a supported fact renders clean; an unsupported
fact keeps the marker. The answering model's own words never reach this
decision: it is asked nothing, and its confidence values are not read.

Fail closed. No verify lane, an exhausted verify budget, a timeout, a reply
the parser refuses, or a verdict list of the wrong length all return ``None``,
and the renderer then marks every line, exactly as before.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from otto.router.budget import BudgetLedger
from otto.router.config import RouterConfig
from otto.router.contract import MalformedProviderOutput, extract_json_object
from otto.router.providers import ProviderClient

VERIFY_LANE = "verify"

_JUDGE_PROMPT = """You are the verification lane for an assistant's reply. You did not write
the reply and you must not rewrite it. Grade each numbered statement below.

For every statement decide:
- "kind": "conversational" if it asserts nothing checkable about the world
  (a greeting, an acknowledgement, a question back, an offer to help, the
  assistant describing what it is or what it can do); otherwise "fact".
- "supported": true only when the statement is a "fact" AND the context
  below shows it to be true. A fact the context does not show is false.
  A conversational statement is always false here.

Your entire output must be one raw JSON object, no fence, no prose:
{{"verdicts": [{{"i": 0, "kind": "conversational", "supported": false}}]}}
with exactly one entry per statement, in order.

Context the assistant was given (the person's message, then memory):
{context}

Statements:
{statements}"""


def _prompt(statements: Sequence[str], context: str) -> str:
    listed = "\n".join(f"{i}. {s}" for i, s in enumerate(statements))
    return _JUDGE_PROMPT.format(context=context, statements=listed)


def parse_verdicts(raw_text: str, count: int) -> tuple[bool, ...]:
    """The verify lane's output as one boolean per statement, or refuse.

    True means the line renders without the marker: it is conversational, or
    it is a fact the lane found supported. Anything else refuses, and the
    caller treats a refusal as "no verdict".
    """
    obj = extract_json_object(raw_text)
    rows = obj.get("verdicts")
    if not isinstance(rows, list) or len(rows) != count:
        raise MalformedProviderOutput(
            f"verdicts missing or {len(rows) if isinstance(rows, list) else 'not a list'} != {count}"
        )
    out: list[bool] = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict) or row.get("i") != i:
            raise MalformedProviderOutput(
                f"verdicts[{i}] out of order or not an object"
            )
        kind = row.get("kind")
        supported = row.get("supported")
        if kind not in ("conversational", "fact") or not isinstance(supported, bool):
            raise MalformedProviderOutput(f"verdicts[{i}] kind or supported malformed")
        out.append(kind == "conversational" or supported)
    return tuple(out)


def judge(
    statements: Sequence[str],
    *,
    context: str,
    config: RouterConfig,
    ledger: BudgetLedger,
    client: ProviderClient,
) -> tuple[bool, ...] | None:
    """One verify-lane call over every statement; ``None`` when no verdict
    could be had, for any reason. Spend lands on the verify lane's ledger."""
    if not statements:
        return None
    lane = config.lanes.get(VERIFY_LANE)
    if lane is None or ledger.exhausted(VERIFY_LANE):
        return None
    try:
        result = client.complete(
            lane.model, _prompt(statements, context), config.retry.timeout_seconds
        )
    except Exception:  # noqa: BLE001 - every failure is "no verdict", never a lost answer
        return None
    ledger.charge(VERIFY_LANE, result.tokens / 1000.0 * lane.cost_per_1k_tokens_usd)
    try:
        return parse_verdicts(result.text, len(statements))
    except (MalformedProviderOutput, json.JSONDecodeError, ValueError):
        return None
