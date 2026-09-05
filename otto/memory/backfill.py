"""One-shot bridge: hindsight's existing memories into the Postgres store.

Moving the synchronous read to ``otto/memory/fast_recall.py`` would
otherwise start it cold. Everything the estate remembers up to now was
written through hindsight (684 memory units, 15,054 links and 371
entities when ``otto/memory/hindsight.py`` was written), and a fast
memory that has forgotten every earlier conversation is worse than a slow
one that has not.

So this reads them back out of the vendor's own list endpoint --
``GET /v1/{org}/banks/{bank}/memories/list``, paged -- and writes each one
into ``otto_facts`` as a ``Fact``. It is idempotent: a hindsight memory
keeps its own id as the fact id, and a row that already exists is
skipped, so an interrupted run is simply restarted.

Provenance survives the crossing. Hindsight's ``metadata`` carries the
``task_id``, ``tier`` and ``taint_capped`` the answering lane put there,
and those become the ``Provenance`` the store's NOT NULL constraint
requires. A memory with no such metadata -- everything the Architect
wrote over its own plugin -- is attributed to hindsight itself rather
than given a borrowed task id, because provenance that names the wrong
source is worse than provenance that admits what it is.

This is a migration, not a service: it runs once, as a Job, against a
bank. It writes nothing back to hindsight and deletes nothing anywhere.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace

from otto.memory import db, store
from otto.memory.config import MemoryConfig, load_config
from otto.memory.embeddings_litellm import provider_from_env, require_http
from otto.memory.hindsight import BANK_ENV, DEFAULT_BANK, ORG, URL_ENV
from otto.memory.models import VALID_TIERS, Fact, Provenance

_LOG = logging.getLogger(__name__)

PAGE_SIZE_ENV = "OTTO_MEMORY_BACKFILL_PAGE_SIZE"
DEFAULT_PAGE_SIZE = 200
TIMEOUT_ENV = "OTTO_MEMORY_BACKFILL_TIMEOUT_S"
DEFAULT_TIMEOUT_S = 60.0
#: What a memory with no task metadata is attributed to. Not a task id
#: from somewhere else: the tier is the one an untrusted-but-recognised
#: source already gets, so nothing is promoted by being old.
FALLBACK_TIER = "T2"


@dataclass
class BackfillReport:
    read: int = 0
    written: int = 0
    skipped_existing: int = 0
    skipped_unusable: int = 0

    def __str__(self) -> str:
        return (
            f"read={self.read} written={self.written} "
            f"already_present={self.skipped_existing} unusable={self.skipped_unusable}"
        )


def _page(base: str, bank: str, limit: int, offset: int, timeout: float) -> dict:
    query = urllib.parse.urlencode({"limit": limit, "offset": offset})
    url = f"{base.rstrip('/')}/v1/{ORG}/banks/{bank}/memories/list?{query}"
    require_http(url, URL_ENV)
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 - require_http refused any scheme but http(s)
        return json.loads(resp.read().decode("utf-8"))


def _to_fact(item: dict) -> Fact | None:
    """One hindsight memory as a ``Fact``, or ``None`` when it carries no
    text worth storing. Never raises on a shape the vendor changes."""
    text = (item.get("text") or "").strip()
    if not text:
        return None
    meta = item.get("metadata")
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except ValueError:
            meta = None
    meta = meta if isinstance(meta, dict) else {}

    tier = meta.get("tier")
    if tier not in VALID_TIERS:
        tier = FALLBACK_TIER
    source = meta.get("task_id") or f"hindsight:{item.get('id')}"
    taint = str(meta.get("taint_capped", "")).lower() == "true"

    try:
        return Fact(
            content=text,
            provenance=Provenance(
                source_envelope_ulid=source, tier_at_capture=tier, taint=taint
            ),
            id=str(item["id"]),
            entity=(item.get("entities") or None),
            attribute=meta.get("surface") or item.get("fact_type") or None,
            value=item.get("document_id"),
        )
    except Exception:  # noqa: BLE001 - a vendor row we cannot make a valid
        # Fact from is skipped and counted, never allowed to end the run.
        _LOG.warning("skipping unusable memory %s", item.get("id"), exc_info=True)
        return None


def run(config: MemoryConfig | None = None) -> BackfillReport:
    config = config or load_config()
    base = os.environ.get(URL_ENV)
    if not base:
        raise RuntimeError(f"{URL_ENV} is unset: nothing to back fill from")
    bank = os.environ.get(BANK_ENV, DEFAULT_BANK)
    limit = int(os.environ.get(PAGE_SIZE_ENV) or DEFAULT_PAGE_SIZE)
    timeout = float(os.environ.get(TIMEOUT_ENV) or DEFAULT_TIMEOUT_S)
    provider = provider_from_env()

    report = BackfillReport()
    offset = 0
    with db.connect(config) as conn:
        while True:
            payload = _page(base, bank, limit, offset, timeout)
            items = payload.get("items") or []
            if not items:
                break
            for item in items:
                report.read += 1
                fact = _to_fact(item)
                if fact is None:
                    report.skipped_unusable += 1
                    continue
                if store.get_fact(conn, fact.id) is not None:
                    report.skipped_existing += 1
                    continue
                if provider is not None:
                    try:
                        fact = replace(fact, embedding=provider.embed(fact.content))
                    except Exception:  # noqa: BLE001 - a fact with no vector is
                        # still found by the full-text arm; store it anyway.
                        _LOG.warning("embedding failed for %s", fact.id, exc_info=True)
                try:
                    store.write_fact(conn, fact)
                except Exception:  # noqa: BLE001 - one bad row never ends a
                    # migration over several hundred; it is counted instead.
                    _LOG.warning("write failed for %s", fact.id, exc_info=True)
                    report.skipped_unusable += 1
                    continue
                report.written += 1
            offset += len(items)
            total = payload.get("total")
            if total is not None and offset >= int(total):
                break
    return report


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    report = run()
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
