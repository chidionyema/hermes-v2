"""The estate's memory, reached over its HTTP API.

Hindsight (vectorize-io, MIT) is the estate's memory provider: the hermes
config declares `memory.provider: hindsight` and the row runs in the cluster
on the one estate Postgres. It already held 684 memory units, 15,054 links
and 371 entities when this module was written (counted on estate-db,
2026-09-04), all of them written by the Architect over its own plugin.

What this module adds is the other two doors. When Telegram moved to the
Universal Event Gateway, the answering lane behind it (`otto.ingress.worker`
into `otto.boot.pipeline.answer_envelope`) had no memory at all: it built a
memory fact, round-tripped it through `Fact.to_row`, and dropped it. Memory
units written per day fell 268 (08-30), 154 (09-02), 14 (09-03), 1 (09-04) --
the estate stopped remembering on the day the door moved.

Two calls, both stdlib, because the vendor's API is the whole contract:

    recall(query)  -> POST .../memories/recall   what we already know
    retain(items)  -> POST .../memories          what just happened

One bank, not one per channel. That is the point: the same person reaching
Otto over Telegram, the web or a voice session lands on the same memories, so
context crosses channels by construction rather than by a sync job.

Configuration is one URL. Unset, both calls are no-ops that return nothing --
a lane with no memory answers exactly as it did before, so nothing that runs
today can be broken by this file. Never raises: a memory that cannot be
reached must never cost the sender their answer.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

_LOG = logging.getLogger(__name__)

#: The self-hosted row, e.g. http://hindsight-api.hindsight.svc:8888
URL_ENV = "OTTO_MEMORY_HINDSIGHT_URL"
#: One bank for the whole estate, so memory is not sharded by channel.
BANK_ENV = "OTTO_MEMORY_BANK"
DEFAULT_BANK = "hermes"
#: The vendor's path carries an org segment; `default` on a self-host.
ORG = "default"

RECALL_TIMEOUT_S = 3.0
RETAIN_TIMEOUT_S = 3.0
#: Recall is prompt budget, not a database dump: the model has to read it.
RECALL_MAX_TOKENS = 1200


@dataclass(frozen=True)
class MemoryConfig:
    url: str | None
    bank: str

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    def endpoint(self, suffix: str) -> str:
        base = (self.url or "").rstrip("/")
        return f"{base}/v1/{ORG}/banks/{self.bank}/memories{suffix}"


def config(env: dict[str, str] | None = None) -> MemoryConfig:
    """Read the one setting. A URL that is not http(s) is treated as unset:
    `file:` and custom schemes are openable by urllib, and a memory endpoint
    that reads the pod's own disk is not a memory endpoint."""
    src = env if env is not None else os.environ
    url = (src.get(URL_ENV) or "").strip() or None
    if url and not url.startswith(("http://", "https://")):
        _LOG.warning(
            "memory.bad_scheme: %s is not an http(s) URL; memory is off", URL_ENV
        )
        url = None
    return MemoryConfig(url=url, bank=(src.get(BANK_ENV) or DEFAULT_BANK).strip())


def _post(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any] | None:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(  # noqa: S310 - config() refuses any scheme but http(s)
        url, data=body, method="POST", headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed http(s) endpoint from config
            raw = resp.read()
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        # Deliberately not re-raised: see the module docstring. The sender's
        # answer does not depend on whether the estate remembered it.
        _LOG.warning("memory.unreachable url=%s error=%s", url, type(exc).__name__)
        return None
    try:
        return json.loads(raw or b"{}")
    except ValueError:
        _LOG.warning("memory.unparseable url=%s", url)
        return None


def recall(query: str, *, cfg: MemoryConfig | None = None) -> str:
    """What the estate already knows that bears on ``query``.

    Returns prompt-ready text, empty when memory is off, unreachable or has
    nothing. The caller pastes it into the prompt; it never becomes an
    instruction, only context.
    """
    cfg = cfg or config()
    if not cfg.enabled or not query.strip():
        return ""
    data = _post(
        cfg.endpoint("/recall"),
        {"query": query, "max_tokens": RECALL_MAX_TOKENS, "prefer_observations": True},
        RECALL_TIMEOUT_S,
    )
    if not isinstance(data, dict):
        return ""
    # The vendor returns rendered context under `context`; older builds and
    # some budgets return the units instead, so both shapes are read.
    text = data.get("context") or data.get("memory_context") or ""
    if not text:
        units = data.get("memories") or data.get("results") or []
        if isinstance(units, list):
            text = "\n".join(
                str(u.get("text") or u.get("content") or "")
                for u in units
                if isinstance(u, dict)
            ).strip()
    return str(text).strip()


def retain(
    content: str,
    *,
    context: str | None = None,
    metadata: dict[str, str] | None = None,
    cfg: MemoryConfig | None = None,
) -> bool:
    """Hand one thing that happened to the estate's memory.

    Asynchronous on the vendor's side: the API acknowledges and its own
    worker extracts, so the answering lane never waits on an extraction
    model. True when the write was accepted.
    """
    cfg = cfg or config()
    if not cfg.enabled or not content.strip():
        return False
    item: dict[str, Any] = {"content": content}
    if context:
        item["context"] = context
    if metadata:
        # The vendor's schema types metadata values as strings.
        item["metadata"] = {k: str(v) for k, v in metadata.items() if v is not None}
    return (
        _post(cfg.endpoint(""), {"items": [item], "async": True}, RETAIN_TIMEOUT_S)
        is not None
    )
