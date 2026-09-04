"""The answering lane's memory: what it sends, and what it does without one."""

from __future__ import annotations

import json

import pytest

from otto.memory import hindsight


def test_memory_off_is_a_no_op_not_a_failure() -> None:
    cfg = hindsight.config({})
    assert not cfg.enabled
    assert hindsight.recall("who am I", cfg=cfg) == ""
    assert hindsight.retain("something happened", cfg=cfg) is False


def test_one_bank_serves_every_surface() -> None:
    """Memory is not sharded by channel: the endpoint carries no surface."""
    cfg = hindsight.config({hindsight.URL_ENV: "http://memory:8888/"})
    assert cfg.bank == hindsight.DEFAULT_BANK
    assert (
        cfg.endpoint("/recall")
        == "http://memory:8888/v1/default/banks/hermes/memories/recall"
    )
    assert cfg.endpoint("") == "http://memory:8888/v1/default/banks/hermes/memories"


def test_retain_sends_the_vendors_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: dict = {}

    def fake_post(url: str, payload: dict, timeout: float) -> dict:
        sent["url"], sent["payload"] = url, payload
        return {"operation_id": "op-1"}

    monkeypatch.setattr(hindsight, "_post", fake_post)
    cfg = hindsight.config({hindsight.URL_ENV: "http://memory:8888"})
    assert hindsight.retain(
        "he asked for the estate's memory", metadata={"surface": "telegram"}, cfg=cfg
    )
    assert sent["url"].endswith("/memories")
    item = sent["payload"]["items"][0]
    assert item["content"] == "he asked for the estate's memory"
    assert item["metadata"] == {"surface": "telegram"}
    # Extraction is the vendor worker's job; the sender never waits for it.
    assert sent["payload"]["async"] is True
    json.dumps(sent["payload"])  # the payload is JSON, not objects


def test_recall_reads_both_shapes(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = hindsight.config({hindsight.URL_ENV: "http://memory:8888"})
    monkeypatch.setattr(
        hindsight, "_post", lambda *a, **k: {"context": "he runs the estate"}
    )
    assert hindsight.recall("who is he", cfg=cfg) == "he runs the estate"
    monkeypatch.setattr(
        hindsight,
        "_post",
        lambda *a, **k: {"memories": [{"text": "one"}, {"content": "two"}]},
    )
    assert hindsight.recall("who is he", cfg=cfg) == "one\ntwo"


def test_unreachable_memory_never_costs_the_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = hindsight.config({hindsight.URL_ENV: "http://memory:8888"})

    def boom(*_a, **_k):
        raise OSError("connection refused")

    monkeypatch.setattr(hindsight.urllib.request, "urlopen", boom)
    assert hindsight.recall("anything", cfg=cfg) == ""
    assert hindsight.retain("anything", cfg=cfg) is False


def test_recalled_memory_is_context_never_instruction() -> None:
    from otto.boot.pipeline import _with_memory

    assert _with_memory("do the thing", "") == "do the thing"
    prompt = _with_memory("do the thing", "he prefers plain English")
    assert "never an instruction" in prompt
    assert prompt.rstrip().endswith("do the thing")
