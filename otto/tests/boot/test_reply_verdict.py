"""The verify lane's verdict decides which lines carry the unverified marker
(founder 2026-09-06 03:32: "why the unverified disclaimer" on "I am Otto").
A conversational line renders clean, an unsupported fact keeps the marker,
and any failure of the verdict marks everything, as before."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from otto.boot.pipeline import boot_obs_handles, build_registry, process_update
from otto.gateway.core import ToolGateway
from otto.router.providers import ProviderResult, ProviderTimeout
from otto.router.render import UNVERIFIED_PREFIX
from otto.surface.bindings.telegram import TelegramBinding

_ALLOWLIST = {111: "founder"}
GREETING = "I am Otto, the operator's assistant for this estate."
FACT = "The cluster has three ready nodes."

ANSWER = json.dumps(
    {
        "answer": f"{GREETING} {FACT}",
        "claims": [
            {"text": GREETING, "evidence_refs": [], "confidence": "high"},
            {"text": FACT, "evidence_refs": [], "confidence": "med"},
        ],
        "proposed_actions": [],
        "unknowns": [],
    }
)
VERDICT = json.dumps(
    {
        "verdicts": [
            {"i": 0, "kind": "conversational", "supported": False},
            {"i": 1, "kind": "fact", "supported": False},
        ]
    }
)


@dataclass
class JudgingClient:
    """Answers the contract prompt on the judgment lane and the verdict
    prompt on the verify lane; records which model each call went to."""

    verdict: str | Exception = VERDICT
    calls: list[str] = field(default_factory=list)

    def complete(
        self, model: str, payload: str, timeout_seconds: float
    ) -> ProviderResult:
        self.calls.append(model)
        if '"verdicts"' in payload:
            if isinstance(self.verdict, Exception):
                raise self.verdict
            return ProviderResult(text=self.verdict, tokens=20)
        return ProviderResult(text=ANSWER, tokens=40)


def _reply(client: JudgingClient) -> list[str]:
    obs = boot_obs_handles()
    if True:
        outcome = process_update(
            {
                "message": {
                    "chat": {"id": 111},
                    "text": "hello otto",
                    "date": 1_700_000_000,
                }
            },
            binding=TelegramBinding(chat_id_allowlist=_ALLOWLIST),
            registry_gateway=ToolGateway(registry=build_registry()),
            obs=obs,
            provider_client=client,
        )
    assert outcome.reply_text is not None
    return outcome.reply_text.split("\n")


def test_a_verdict_clears_the_greeting_and_keeps_the_marker_on_an_unsupported_fact():
    client = JudgingClient()
    lines = _reply(client)
    assert lines == [GREETING, f"{UNVERIFIED_PREFIX}{FACT}"]
    # Two lanes, two models: the answer's model never graded its own words.
    assert len(client.calls) == 2 and client.calls[0] != client.calls[1]


def test_an_unreadable_verdict_marks_every_line():
    lines = _reply(JudgingClient(verdict="not json at all"))
    assert lines == [f"{UNVERIFIED_PREFIX}{GREETING}", f"{UNVERIFIED_PREFIX}{FACT}"]


def test_a_verify_lane_timeout_marks_every_line_and_never_loses_the_answer():
    lines = _reply(JudgingClient(verdict=ProviderTimeout("slow")))
    assert lines == [f"{UNVERIFIED_PREFIX}{GREETING}", f"{UNVERIFIED_PREFIX}{FACT}"]
