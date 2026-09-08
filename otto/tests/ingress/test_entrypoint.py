"""The gateway process boots fail-closed, or it does not boot.

The deployment in the platform repository names ``python -m otto.ingress``
and hands it a port, a database and a collector endpoint. These tests are
the control on that contract: if the module stops reading one of those,
or starts listening before the database is proved, the pod would come up
green and answer 401 to every real customer.
"""

from __future__ import annotations

import asyncio
import inspect
import re

import pytest

from otto.ingress import pg_store
from otto.ingress.__main__ import (
    DEFAULT_PORT,
    PORT_ENV,
    PortNotUsable,
    build_deps,
    port_from_env,
)
from otto.ingress.pg_store import (
    DatabaseNotConfigured,
    PostgresChannelBindingStore,
    dsn_from_env,
)
from otto.ingress.store import POSTGRES_DDL, ChannelBindingStore
from otto.obs.config import ENDPOINT_ENV, MODE_ENV
from otto.obs.core import ObsBootError

PASSWORD_UNDER_TEST = "not-the-real-one"  # noqa: S105 - a test fixture value


def _env(tmp_path, **overrides) -> dict[str, str]:
    password_file = tmp_path / "password"
    password_file.write_text(PASSWORD_UNDER_TEST + "\n")
    env = {
        pg_store.HOST_ENV: "binding-db",
        pg_store.NAME_ENV: "otto_ingress",
        pg_store.USER_ENV: "otto_ingress",
        pg_store.PASSWORD_FILE_ENV: str(password_file),
    }
    env.update({k: v for k, v in overrides.items() if v is not None})
    for key, value in overrides.items():
        if value is None:
            env.pop(key, None)
    return env


# --- the port -------------------------------------------------------------


def test_the_port_falls_back_to_the_service_target_port() -> None:
    assert port_from_env({}) == DEFAULT_PORT


def test_the_deployment_names_the_port() -> None:
    assert port_from_env({PORT_ENV: "9090"}) == 9090


@pytest.mark.parametrize("value", ["eighty-eighty", "0", "70000", "-1"])
def test_a_port_that_is_not_a_port_is_refused_at_boot(value: str) -> None:
    with pytest.raises(PortNotUsable):
        port_from_env({PORT_ENV: value})


# --- the database ---------------------------------------------------------


@pytest.mark.parametrize(
    "missing", [pg_store.HOST_ENV, pg_store.NAME_ENV, pg_store.USER_ENV]
)
def test_a_half_configured_database_refuses_boot_by_name(
    tmp_path, missing: str
) -> None:
    with pytest.raises(DatabaseNotConfigured, match=missing):
        dsn_from_env(_env(tmp_path, **{missing: None}))


def test_a_password_that_the_secret_store_has_not_projected_refuses_boot(
    tmp_path,
) -> None:
    env = _env(tmp_path)
    env[pg_store.PASSWORD_FILE_ENV] = str(tmp_path / "never-written")
    with pytest.raises(DatabaseNotConfigured, match="not a file"):
        dsn_from_env(env)


def test_the_password_comes_from_the_mounted_file_and_not_the_environment(
    tmp_path,
) -> None:
    """The estate's admission policy refuses a Secret delivered as pod
    environment, so the only road in is a mounted file."""
    dsn = dsn_from_env(_env(tmp_path))
    assert f"password={PASSWORD_UNDER_TEST}" in dsn
    assert "host=binding-db" in dsn
    assert "dbname=otto_ingress" in dsn


def test_the_password_is_never_in_a_refusal_message(tmp_path) -> None:
    env = _env(tmp_path, **{pg_store.HOST_ENV: None})
    with pytest.raises(DatabaseNotConfigured) as caught:
        dsn_from_env(env)
    assert PASSWORD_UNDER_TEST not in str(caught.value)


# --- the store keeps the contract the gateway depends on ------------------


def test_the_postgres_store_answers_every_method_the_gateway_calls() -> None:
    for name, expected in inspect.getmembers(
        ChannelBindingStore, predicate=inspect.isfunction
    ):
        if name.startswith("_"):
            continue
        actual = getattr(PostgresChannelBindingStore, name, None)
        assert actual is not None, (
            f"PostgresChannelBindingStore is missing {name}; the gateway "
            "would fail on a live event, not in this suite"
        )
        assert list(inspect.signature(actual).parameters) == list(
            inspect.signature(expected).parameters
        ), f"{name} drifted from the store contract"


def test_every_column_the_statements_touch_is_declared_in_the_schema() -> None:
    """The database and the statements are written out separately so a
    reader can see the real table; this is what stops them drifting."""
    declared = set(
        re.findall(r"^\s{4}([a-z_]+)\s+(?:TEXT|TIMESTAMPTZ)", POSTGRES_DDL, re.M)
    )
    assert declared, "the schema stopped declaring columns in a readable shape"
    for statement in (pg_store.INSERT_SQL, pg_store.LOOKUP_SQL, pg_store.STATUS_SQL):
        used = set(
            re.findall(
                r"\b(tenant_id|channel|external_id|secret_ref|token_fingerprint|status|created_at)\b",
                statement,
            )
        )
        assert used <= declared, f"{used - declared} is not a column: {statement}"


def test_the_lookup_still_filters_on_the_channel() -> None:
    """A credential lifted from one channel must not open a binding on
    another. The SQLite store has the same control; this is the same
    property at the layer that actually runs in the cluster."""
    assert "channel = %s" in pg_store.LOOKUP_SQL
    assert "token_fingerprint = %s" in pg_store.LOOKUP_SQL


def test_the_conflict_target_matches_the_primary_key() -> None:
    assert "ON CONFLICT (channel, external_id)" in pg_store.INSERT_SQL
    assert "PRIMARY KEY (channel, external_id)" in POSTGRES_DDL


# --- boot order -----------------------------------------------------------


def test_a_gateway_that_cannot_be_seen_does_not_start(monkeypatch) -> None:
    """LAW 50. The collector is proved before the database and long before
    the socket, so a workload that would run dark refuses instead."""
    monkeypatch.delenv(ENDPOINT_ENV, raising=False)
    monkeypatch.delenv(MODE_ENV, raising=False)
    loop = asyncio.new_event_loop()
    try:
        with pytest.raises(ObsBootError):
            build_deps(loop)
    finally:
        loop.close()


def test_the_database_is_proved_before_the_socket_opens(monkeypatch) -> None:
    """With the collector satisfied and the database absent, boot fails on
    the database. Nothing has listened by then."""
    monkeypatch.setenv(MODE_ENV, "test")
    for name in (pg_store.HOST_ENV, pg_store.NAME_ENV, pg_store.USER_ENV):
        monkeypatch.delenv(name, raising=False)
    loop = asyncio.new_event_loop()
    try:
        with pytest.raises(DatabaseNotConfigured):
            build_deps(loop)
    finally:
        loop.close()
