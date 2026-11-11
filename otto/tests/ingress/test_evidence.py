"""The founder widget's evidence store: verified, fail-closed briefs.

The ``FounderEvidence`` implementation reads the catalogue instruction
ledger and turns it into the widget's ``chat_data`` object. Every test
here is offline: the DB reads are monkeypatched, and only the mapping
from ledger facts to widget fields is asserted, plus the fail-closed
contract that a down source is a stale brief and never an exception.
"""

from __future__ import annotations

import json

from otto.ingress.evidence import (
    CATALOG_HOST_ENV,
    CATALOG_NAME_ENV,
    CATALOG_PASSWORD_FILE_ENV,
    CATALOG_USER_ENV,
    DEFAULT_NO_EVIDENCE,
    CatalogNotConfigured,
    FounderEvidenceStore,
    catalog_dsn_from_env,
)


def _store(monkeypatch, *, counts=(3, 2, 1), latest=("instr-7", "Ship the router")):
    store = FounderEvidenceStore("host=example dbname=estate")
    monkeypatch.setattr(store, "_counts", lambda: tuple(counts))
    monkeypatch.setattr(store, "_latest", lambda: latest)
    return store


# -- DSN construction -----------------------------------------------------


def test_catalog_dsn_from_env_requires_host_name_user_and_password_file() -> None:
    for missing in (CATALOG_HOST_ENV, CATALOG_NAME_ENV, CATALOG_USER_ENV):
        env = {
            CATALOG_HOST_ENV: "db",
            CATALOG_NAME_ENV: "estate",
            CATALOG_USER_ENV: "otto",
            CATALOG_PASSWORD_FILE_ENV: "/tmp/pw",
        }
        del env[missing]
        try:
            catalog_dsn_from_env(env)
        except CatalogNotConfigured:
            pass
        else:
            raise AssertionError(f"missing {missing} did not raise")


def test_catalog_dsn_from_env_requires_password_file_and_that_it_exists(
    monkeypatch, tmp_path,
) -> None:
    env = {
        CATALOG_HOST_ENV: "db",
        CATALOG_NAME_ENV: "estate",
        CATALOG_USER_ENV: "otto",
    }
    # No password-file env var at all.
    try:
        catalog_dsn_from_env(env)
    except CatalogNotConfigured:
        pass
    else:
        raise AssertionError("missing password-file env did not raise")

    # Points at a path that does not exist.
    env[CATALOG_PASSWORD_FILE_ENV] = str(tmp_path / "nope")
    try:
        catalog_dsn_from_env(env)
    except CatalogNotConfigured:
        pass
    else:
        raise AssertionError("missing file did not raise")

    # A real file: DSN is built and carries the password from the file.
    pw = tmp_path / "pw"
    pw.write_text("secret-value\n")
    env[CATALOG_PASSWORD_FILE_ENV] = str(pw)
    dsn = catalog_dsn_from_env(env)
    assert "secret-value" in dsn
    assert "dbname=estate" in dsn


# -- brief contract -------------------------------------------------------


def test_brief_maps_ledger_counts_to_widget_fields(monkeypatch) -> None:
    store = _store(monkeypatch, counts=(3, 2, 1), latest=("instr-7", "Ship the router"))
    brief = store.brief()

    assert brief["delivered"] == 3
    assert brief["active"] == 2
    assert brief["issues"] == 1
    assert brief["r"] >= 1
    assert brief["at"] == brief["r"] * 1000
    assert "proven" in brief["headline"]
    assert brief["id"] == "instr-7"
    assert brief["proof"] == "Ship the router"


def test_brief_is_fail_closed_when_the_read_raises(monkeypatch) -> None:
    store = FounderEvidenceStore("host=example dbname=estate")

    def boom() -> tuple[int, int, int]:
        raise ConnectionError("db down")

    monkeypatch.setattr(store, "_counts", boom)
    assert store.brief() == dict(DEFAULT_NO_EVIDENCE)


def test_brief_never_raises_even_with_no_latest(monkeypatch) -> None:
    # The _latest read fails: the id/proof become neutral dashes, but the
    # counts still render — proof is absent, never fabricated.
    store = _store(monkeypatch, counts=(0, 0, 0), latest=(None, None))
    brief = store.brief()
    assert brief["delivered"] == 0
    assert brief["id"] == "—"
    assert brief["proof"] == "—"


def test_brief_stays_within_the_widget_field_budgets(monkeypatch) -> None:
    # A pathological latest prompt must not blow the widget's 130-byte
    # proof budget; it falls back to a dash rather than truncate.
    long_prompt = "x" * 500
    store = _store(monkeypatch, counts=(0, 0, 0), latest=("instr-1", long_prompt))
    brief = store.brief()
    assert brief["proof"] == "—"

    # The whole packet, serialised the way the transport does, must fit
    # Telegram's 1024-byte cap even with large (but legal) counts.
    store2 = _store(monkeypatch, counts=(999999, 888888, 777777), latest=("i", "p"))
    packet = store2.brief()
    serialised = json.dumps(packet, separators=(",", ":"))
    assert len(serialised.encode("utf-8")) <= 1024


# -- fail-closed default is exactly the gateway's shape ------------------


def test_default_no_evidence_has_the_widgets_contract_shape() -> None:
    # The gateway's own _safe_brief returns this same shape when the seam
    # is absent; the store returns it when the source is down. One shape.
    assert set(DEFAULT_NO_EVIDENCE) == {
        "r",
        "at",
        "headline",
        "delivered",
        "active",
        "issues",
        "priority",
        "id",
        "proof",
    }
    assert DEFAULT_NO_EVIDENCE["r"] == 0
    assert DEFAULT_NO_EVIDENCE["at"] == 0
    assert DEFAULT_NO_EVIDENCE["delivered"] is None
    assert DEFAULT_NO_EVIDENCE["active"] is None
    assert DEFAULT_NO_EVIDENCE["issues"] is None
