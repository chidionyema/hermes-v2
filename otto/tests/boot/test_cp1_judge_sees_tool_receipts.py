"""Crew #892 CP1 — the verify judge sees the turn's tool receipts.

Defect on origin/main: ``answer_envelope`` grades each claim against a
context of the inbound message plus recalled memory
(``_with_memory(asked or noted_text, recalled)``). The tool results the
answering model ran to learn a fact (``terminal`` and friends, via
``_build_tool_loop.execute``) never reach the judge: the tools run inside
the provider's own loop and the receipt strings pass through the executor
closure and are discarded. So a claim that *restates* a tool result is
judged unsupported simply because the verifying model never saw the receipt
— it keeps the unverified marker the receipt should have cleared
(founder 2026-09-06 03:32: "why the unverified disclaimer").

Both tests drive one *real* tool turn through the boot pipeline with an
injected gateway offering a real ``terminal`` hand, egress stubbed so the
local ``LiteLLMClient`` runs its tool loop. The first asserts the receipt
lands in the context handed to ``reply_judge.judge`` (crew#892 CP1's "one
tool receipt and one claim that restates it; the verdict is supported").
"""

from __future__ import annotations

import json

import pytest

from otto.boot.pipeline import (
    _receipts_context,
    boot_obs_handles,
    build_registry,
    process_update,
)
from otto.gateway.core import ToolGateway
from otto.gateway.registry import Tier, ToolRegistry, ToolSpec
from otto.router.providers import LiteLLMClient
from otto.surface.bindings.telegram import TelegramBinding
from otto.verify import reply_judge as reply_judge_module

_ALLOWLIST = {111: "founder"}
MESSAGE = "how many ready nodes does the cluster have?"
RECEIPT = "3 ready nodes"
RESTATEMENT = "The cluster has three ready nodes."


def test_receipts_context_is_newest_first_and_capped() -> None:
    """The receipts handed to the judge come newest-first, and the cap drops
    the oldest when the tool loop ran long — never the newest."""
    block = _receipts_context(
        [(1, "terminal", "first result"), (2, "terminal", "latest result")],
        cap_tokens=10_000,
    )
    head, _, tail = block.partition("\n")
    assert "latest result" in head and "first result" in tail
    # Capped hard: a tiny budget keeps only the newest receipt.
    capped = _receipts_context(
        [(1, "terminal", "old " * 1000), (2, "terminal", "newest")],
        cap_tokens=8,  # ~32 characters: room for the short newest line only
    )
    assert "newest" in capped
    assert "old " not in capped
    # No tools ran: no receipts block at all.
    assert _receipts_context([], cap_tokens=10_000) == ""


def _registry_with_terminal() -> ToolRegistry:
    """A gateway registry whose one real hand is ``terminal`` at T2.

    ``answer_envelope`` levels a trusted founder's ceiling to T2 and offers
    every registry tool at tier <= ceiling except the ``note`` probe, so a
    T2 ``terminal`` here is genuinely offered to the answering model and the
    conversation's tool loop really runs.
    """

    def _terminal(args: dict) -> dict:  # noqa: ARG001 - config/echo fixture
        return {"result": RECEIPT}

    # ``build_registry`` provides the ``note`` probe the pipeline's first
    # gateway call (``answer_envelope`` probes ``note`` to decide trust by
    # omission) is made against; ``terminal`` is the one real hand added so
    # the answering model's tool loop genuinely runs.
    registry = build_registry()
    registry.register(
        ToolSpec(
            name="terminal",
            tier=Tier.T2,
            input_schema={
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
                "additionalProperties": False,
            },
            handler=_terminal,
        )
    )
    return registry


def _answer_body() -> str:
    return json.dumps(
        {
            "answer": MESSAGE,
            "claims": [
                {"text": RESTATEMENT, "evidence_refs": [], "confidence": "high"}
            ],
            "proposed_actions": [],
            "unknowns": [],
        }
    )


def _tool_call_turn() -> dict:
    """One HTTP turn in which the model asks for ``terminal``."""
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_receipt",
                            "type": "function",
                            "function": {
                                "name": "terminal",
                                "arguments": '{"command": "kubectl get nodes"}',
                            },
                        }
                    ],
                }
            }
        ],
        "usage": {"total_tokens": 7},
    }


def _answer_turn() -> dict:
    return {
        "choices": [{"message": {"content": _answer_body()}}],
        "usage": {"total_tokens": 40},
    }


def _attach_urlopen(monkeypatch: pytest.MonkeyPatch, queue: list[dict]) -> None:
    """Stub egress so the real ``LiteLLMClient`` runs its tool loop locally."""
    monkeypatch.setenv("LITELLM_API_KEY", "test-old-do-not-use")
    fallback = {
        "choices": [{"message": {"content": "final"}}],
        "usage": {"total_tokens": 3},
    }

    class _Resp:
        def read(self) -> bytes:
            return json.dumps(queue.pop(0) if queue else fallback).encode()

        def __enter__(self) -> "_Resp":
            return self

        def __exit__(self, *a: object) -> None:
            return None

    def _fake_open(req, timeout: float = 1.0):  # noqa: ARG001
        return _Resp()

    monkeypatch.setattr("urllib.request.urlopen", _fake_open)


def test_a_tool_receipt_reaches_the_judge_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The verify judge is called with the turn's tool receipt in its
    context, so a claim restating the receipt is supportable."""
    # Real client tool loop, two HTTP turns: a terminal tool call the
    # executor answers with the receipt, then a text answer. A third (the
    # judge's own model call) is absorbed by the fallback reply.
    _attach_urlopen(
        monkeypatch,
        [_tool_call_turn(), _answer_turn()],
    )

    judged_contexts: list[str] = []

    def _spy(statements, *, context: str, config, ledger, client):
        judged_contexts.append(context)
        # supported: the restating claim is gradeable against the receipt
        return tuple(True for _ in statements)

    monkeypatch.setattr(reply_judge_module, "judge", _spy)

    outcome = process_update(
        {
            "message": {
                "chat": {"id": 111},
                "text": MESSAGE,
                "date": 1_700_000_000,
            }
        },
        binding=TelegramBinding(chat_id_allowlist=_ALLOWLIST),
        registry_gateway=ToolGateway(registry=_registry_with_terminal()),
        obs=boot_obs_handles(),
        provider_client=LiteLLMClient(),
    )

    assert outcome.reply_text is not None
    # The verify lane graded the turn exactly once.
    assert len(judged_contexts) == 1, (
        "the verify judge must be called once for a completed unverified turn"
    )
    # CP1: the terminal receipt the answering model relied on is part of the
    # judge's context, so the verifying model can see that "three ready
    # nodes" came from a tool, not from the model's own guess. The claim text
    # itself is deliberately absent — the claim being graded is never fed back
    # to the grader (otto/verify/reply_judge.py P1).
    assert RECEIPT in judged_contexts[0], (
        "the judge's context must carry the turn's tool receipt"
    )
    # The receipt is carried under an explicit tool-results heading so a
    # grader cannot confuse an observed fact with the model's own guess.
    assert "[terminal]" in judged_contexts[0]


def test_pipeline_with_no_tool_result_still_grades_without_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A turn with no tool call is unchanged: the judge still runs, still
    sees the message context, and no empty-receipts block is appended."""
    judged_contexts: list[str] = []

    def _spy(statements, *, context: str, config, ledger, client):
        judged_contexts.append(context)
        return tuple(False if s == RESTATEMENT else True for s in statements)

    monkeypatch.setattr(reply_judge_module, "judge", _spy)
    # Just the answer turn; no tool-call turn.
    _attach_urlopen(monkeypatch, [_answer_turn()])

    outcome = process_update(
        {
            "message": {
                "chat": {"id": 111},
                "text": MESSAGE,
                "date": 1_700_000_000,
            }
        },
        binding=TelegramBinding(chat_id_allowlist=_ALLOWLIST),
        registry_gateway=ToolGateway(registry=_registry_with_terminal()),
        obs=boot_obs_handles(),
        provider_client=LiteLLMClient(),
    )

    assert outcome.reply_text is not None
    assert len(judged_contexts) == 1
