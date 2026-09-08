"""The binding table and the secret resolver, on their own.

Two properties a buyer's engineer will look for first: the table holds no
secret material, and an error about a secret never quotes one.
"""

from __future__ import annotations

import pytest

from otto.ingress.secrets import EnvSecretResolver, SecretNotFound, env_var_name
from otto.ingress.store import (
    ACTIVE,
    DISABLED,
    ChannelBinding,
    SqliteChannelBindingStore,
    fingerprint,
)

from .conftest import (
    ACME,
    ACME_TELEGRAM_REF,
    ACME_TELEGRAM_TOKEN,
    GLOBEX,
    GLOBEX_TELEGRAM_REF,
    GLOBEX_TELEGRAM_TOKEN,
)


def test_the_table_never_holds_the_credential_itself(store) -> None:
    rows = store._conn.execute("SELECT * FROM channel_binding").fetchall()
    dumped = " ".join(str(value) for row in rows for value in tuple(row))

    assert ACME_TELEGRAM_TOKEN not in dumped, (
        "a credential reached the binding table; the table stores a "
        "reference and a one-way fingerprint, and nothing else"
    )
    assert fingerprint(ACME_TELEGRAM_TOKEN) in dumped


def test_a_credential_is_found_by_its_fingerprint(store) -> None:
    found = store.find_by_credential("telegram", ACME_TELEGRAM_TOKEN)
    assert found is not None
    assert found.tenant_id == ACME
    assert found.secret_ref == ACME_TELEGRAM_REF
    assert found.active


def test_an_unknown_credential_finds_nothing(store) -> None:
    assert store.find_by_credential("telegram", "some-other-token") is None


def test_the_same_credential_on_a_different_channel_finds_nothing(store) -> None:
    """The lookup is keyed on the pair, so a token leaked from one channel
    cannot be replayed into another."""
    assert store.find_by_credential("http", ACME_TELEGRAM_TOKEN) is None


def test_re_registering_a_connection_rotates_its_credential() -> None:
    """Rotation is the same single write as onboarding: the old value stops
    working the moment the new one is stored."""
    store = SqliteChannelBindingStore()
    binding = ChannelBinding(
        tenant_id=GLOBEX,
        channel="telegram",
        external_id="globex-bot",
        secret_ref=GLOBEX_TELEGRAM_REF,
    )
    store.register(binding, credential="first-token")
    store.register(binding, credential=GLOBEX_TELEGRAM_TOKEN)

    assert store.find_by_credential("telegram", "first-token") is None
    assert store.find_by_credential("telegram", GLOBEX_TELEGRAM_TOKEN) is not None
    store.close()


def test_disabling_keeps_the_row_and_flips_the_status(store) -> None:
    store.set_status("telegram", "acme-bot", DISABLED)
    found = store.find_by_credential("telegram", ACME_TELEGRAM_TOKEN)
    assert found is not None
    assert not found.active

    store.set_status("telegram", "acme-bot", ACTIVE)
    found = store.find_by_credential("telegram", ACME_TELEGRAM_TOKEN)
    assert found is not None
    assert found.active


@pytest.mark.parametrize("field", ["tenant_id", "channel", "external_id", "secret_ref"])
def test_a_binding_missing_any_of_its_four_facts_is_refused(field: str) -> None:
    fields = {
        "tenant_id": ACME,
        "channel": "telegram",
        "external_id": "acme-bot",
        "secret_ref": ACME_TELEGRAM_REF,
    }
    fields[field] = ""
    with pytest.raises(ValueError, match=field):
        ChannelBinding(**fields)


def test_a_reference_maps_to_one_predictable_environment_key() -> None:
    assert (
        env_var_name("vault://otto/acme/telegram")
        == "OTTO_CHANNEL_SECRET_VAULT_OTTO_ACME_TELEGRAM"
    )


def test_a_missing_secret_raises_naming_the_reference_and_not_the_value() -> None:
    resolver = EnvSecretResolver(environ={})
    with pytest.raises(SecretNotFound) as raised:
        resolver.resolve(ACME_TELEGRAM_REF)

    assert ACME_TELEGRAM_REF in str(raised.value)


def test_a_present_secret_resolves(secret_env) -> None:
    resolver = EnvSecretResolver(environ=secret_env)
    assert resolver.resolve(ACME_TELEGRAM_REF) == ACME_TELEGRAM_TOKEN
