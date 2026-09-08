"""The answering path records the conversation it just had.

crew#920 item 3, the wiring half. ``otto/tests/cp4/test_conversation_record.py``
grades the table against a real Postgres; this grades that
``answer_envelope`` actually calls it, with the sender's own words and the
text that went back, and that a denied sender is not written into the
customer's transcript.
"""

from __future__ import annotations

import json

import pytest

from otto.boot import pipeline as pipeline_module
from otto.boot.pipeline import boot_obs_handles, build_registry, process_update
from otto.gateway.core import ToolGateway
from otto.surface.bindings.telegram import TelegramBinding
from otto.verify import reply_judge as reply_judge_module

_ALLOWLIST = {111: "founder"}
MESSAGE = "what is the estate's one scheduler?"
CLAIM = "Dagster is the estate's one scheduler."


def _answer_turn() -> dict:
    return {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "answer": CLAIM,
                            "claims": [
                                {
                                    "text": CLAIM,
                                    "evidence_refs": [],
                                    "confidence": "high",
                                }
                            ],
                            "proposed_actions": [],
                            "unknowns": [],
                        }
                    )
                }
            }
        ],
        "usage": {"total_tokens": 40},
    }


def _attach_urlopen(monkeypatch: pytest.MonkeyPatch, queue: list[dict]) -> None:
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

    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=1.0: _Resp())


def _capture(monkeypatch: pytest.MonkeyPatch) -> list:
    recorded: list = []

    def _spy(turn, config=None) -> bool:
        recorded.append(turn)
        return True

    monkeypatch.setattr(pipeline_module.conversation, "record", _spy)
    return recorded


def _run(monkeypatch: pytest.MonkeyPatch, chat_id: int):
    from otto.router.providers import LiteLLMClient

    return process_update(
        {"message": {"chat": {"id": chat_id}, "text": MESSAGE, "date": 1_700_000_000}},
        binding=TelegramBinding(chat_id_allowlist=_ALLOWLIST),
        registry_gateway=ToolGateway(registry=build_registry()),
        obs=boot_obs_handles(),
        provider_client=LiteLLMClient(),
    )


def test_an_answered_message_is_recorded_with_both_sides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One row per answered task, holding the question, the reply that went
    back, and the attribution needed to explain it later."""
    _attach_urlopen(monkeypatch, [_answer_turn()])
    monkeypatch.setattr(
        reply_judge_module,
        "judge",
        lambda statements, **kw: tuple(True for _ in statements),
    )
    recorded = _capture(monkeypatch)

    outcome = _run(monkeypatch, 111)

    assert outcome.reply_text is not None
    assert len(recorded) == 1
    turn = recorded[0]
    assert turn.asked == MESSAGE
    assert turn.answered == outcome.reply_text
    assert turn.tenant_id and turn.task_ulid
    assert turn.surface == "telegram"
    assert turn.lane and turn.model
    assert turn.outcome_state == "completed_unverified"
    # The verify lane ran and cleared the one claim.
    assert turn.verified is True
    assert turn.claims_total == 1 and turn.claims_clean == 1


def test_a_thin_answer_is_recorded_as_judged_and_not_clean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reply the verify lane could not support is recorded as verified
    False -- not as an absent verdict, which is a different fact."""
    _attach_urlopen(monkeypatch, [_answer_turn()])
    monkeypatch.setattr(
        reply_judge_module,
        "judge",
        lambda statements, **kw: tuple(False for _ in statements),
    )
    recorded = _capture(monkeypatch)

    _run(monkeypatch, 111)

    assert len(recorded) == 1
    assert recorded[0].verified is False
    assert recorded[0].claims_clean == 0


def test_an_unrecognised_sender_is_not_written_into_the_transcript(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A gateway denial sends no reply and records no conversation.

    An unrecognised chat is capped below the gateway's tool tier and denied,
    and this path must not become a way for a stranger to write arbitrary
    text into a customer's stored transcript.
    """
    _attach_urlopen(monkeypatch, [_answer_turn()])
    recorded = _capture(monkeypatch)

    outcome = _run(monkeypatch, 999)

    assert outcome.reply_text is None
    assert recorded == []


def test_a_failing_recorder_never_costs_the_sender_their_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``record`` is best effort by contract; the reply is composed and
    returned even when the store is unreachable."""
    _attach_urlopen(monkeypatch, [_answer_turn()])
    monkeypatch.setattr(
        reply_judge_module,
        "judge",
        lambda statements, **kw: tuple(True for _ in statements),
    )
    # The real record(), pointed at a database that cannot be reached.
    monkeypatch.setenv(
        "OTTO_MEMORY_DATABASE_URL", "postgresql://nobody@127.0.0.1:1/none"
    )

    outcome = _run(monkeypatch, 111)

    assert outcome.reply_text is not None
