"""The one door: routing, refusals, and channel independence."""

from __future__ import annotations

import pytest

from otto.ingress.gateway import (
    ACCEPTED,
    BAD_REQUEST,
    FORBIDDEN,
    NOTHING_TO_DO,
    NOT_FOUND,
    UNAUTHORIZED,
    UNAVAILABLE,
    EventGateway,
)
from otto.ingress.secrets import EnvSecretResolver
from otto.ingress.store import DISABLED
from otto.obs import instrument
from otto.spine.envelope import TaskSource, TrustTag

from .conftest import (
    ACME,
    ACME_HTTP_TOKEN,
    ACME_TELEGRAM_REF,
    ACME_TELEGRAM_TOKEN,
    http_body,
    telegram_body,
)


def _telegram_headers(token: str = ACME_TELEGRAM_TOKEN) -> dict[str, str]:
    return {"X-Telegram-Bot-Api-Secret-Token": token}


def _http_headers(token: str = ACME_HTTP_TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# -- the happy path -------------------------------------------------------


def test_a_recognised_token_resolves_its_customer_and_publishes_a_task(
    gateway: EventGateway, publisher
) -> None:
    result = gateway.handle(
        "telegram", _telegram_headers(), telegram_body("do a thing")
    )

    assert result.status == ACCEPTED
    assert result.tenant_id == ACME
    assert publisher.last.tenant_id == ACME
    assert publisher.last.input == "do a thing"
    assert publisher.last.task_id == result.task_id
    assert result.subject == "otto.task.v1.submitted"


def test_the_published_task_names_its_channel_only_in_provenance(
    gateway: EventGateway, publisher
) -> None:
    """The agent lanes must be able to do their work without ever reading
    the channel. It is recorded for audit, and nowhere a lane branches on."""
    gateway.handle("telegram", _telegram_headers(), telegram_body())
    envelope = publisher.last

    assert "channel:telegram" in envelope.provenance
    payload = envelope.model_dump(mode="json", by_alias=True)
    channel_fields = [
        key
        for key, value in payload.items()
        if key != "provenance" and "telegram" in str(value).lower()
    ]
    assert channel_fields == ["source"], (
        "only 'source' and the audit provenance may mention the channel; "
        f"these also did: {channel_fields}"
    )


def test_two_channels_produce_the_same_envelope_for_the_same_content(
    gateway: EventGateway, publisher
) -> None:
    """The contract that makes a third channel free: Telegram and a plain
    HTTP caller differ in their transport and in nothing else."""
    gateway.handle("telegram", _telegram_headers(), telegram_body("same words"))
    gateway.handle("http", _http_headers(), http_body("same words"))

    telegram_task, http_task = publisher.published
    # ``reply_to`` joins this set for the same reason ``source`` is in it:
    # it is the transport's own address for the sender, written in the
    # channel's address space and never read by a lane. Telegram has one
    # (a chat id) and a plain HTTP caller has none, and that difference is
    # the transport differing, which is what this test permits.
    ignore = {"task_id", "created_at", "provenance", "source", "reply_to"}
    assert {
        k: v
        for k, v in telegram_task.model_dump(mode="json").items()
        if k not in ignore
    } == {k: v for k, v in http_task.model_dump(mode="json").items() if k not in ignore}
    assert telegram_task.source is TaskSource.telegram
    assert http_task.source is TaskSource.api


def test_an_unrecognised_sender_is_taint_capped_not_trusted(
    gateway: EventGateway, publisher
) -> None:
    """Authenticating the customer's channel does not authenticate the
    person on the far side of it: they stay untrusted and capped."""
    gateway.handle("telegram", _telegram_headers(), telegram_body())

    assert TrustTag.untrusted in publisher.last.taint
    assert publisher.last.is_taint_capped


# -- refusals -------------------------------------------------------------


def test_an_unknown_channel_is_not_found(gateway: EventGateway, publisher) -> None:
    result = gateway.handle("carrier-pigeon", _telegram_headers(), telegram_body())
    assert result.status == NOT_FOUND
    assert publisher.published == []


def test_a_request_with_no_credential_is_refused(
    gateway: EventGateway, publisher
) -> None:
    result = gateway.handle("telegram", {}, telegram_body())
    assert result.status == UNAUTHORIZED
    assert publisher.published == []


def test_an_unknown_token_is_refused_indistinguishably_from_no_token(
    gateway: EventGateway,
) -> None:
    """A prober must not be able to tell a wrong token from an
    unregistered one — the difference would confirm which tokens exist."""
    no_token = gateway.handle("telegram", {}, telegram_body())
    wrong_token = gateway.handle(
        "telegram", _telegram_headers("not-a-real-token"), telegram_body()
    )
    assert wrong_token.status == no_token.status == UNAUTHORIZED
    assert wrong_token.reason == no_token.reason
    assert wrong_token.tenant_id is None


def test_one_customers_token_never_reaches_another_customers_channel(
    gateway: EventGateway, publisher
) -> None:
    """The HTTP customer's bearer token presented to the Telegram door is
    simply not a Telegram credential, whoever owns it."""
    result = gateway.handle(
        "telegram", _telegram_headers(ACME_HTTP_TOKEN), telegram_body()
    )
    assert result.status == UNAUTHORIZED
    assert publisher.published == []


def test_a_disabled_connection_is_refused_without_deleting_it(
    gateway: EventGateway, store, publisher
) -> None:
    store.set_status("telegram", "acme-bot", DISABLED)
    result = gateway.handle("telegram", _telegram_headers(), telegram_body())

    assert result.status == FORBIDDEN
    assert result.tenant_id == ACME  # the customer is known; the door is shut
    assert publisher.published == []
    assert store.find_by_credential("telegram", ACME_TELEGRAM_TOKEN) is not None


def test_a_missing_secret_is_the_platforms_fault_not_the_callers(
    store, publisher
) -> None:
    """A resolver that cannot produce the secret must not answer 401: the
    caller would go hunting for a token problem they do not have."""
    gateway = EventGateway(
        store=store,
        secrets=EnvSecretResolver(environ={}),
        publisher=publisher,
        obs=instrument("ingress"),
    )
    result = gateway.handle("telegram", _telegram_headers(), telegram_body())

    assert result.status == UNAVAILABLE
    assert publisher.published == []


@pytest.mark.parametrize(
    "body", [b"not json at all", b'"a string, not an object"', b"[1, 2, 3]"]
)
def test_a_body_that_is_not_a_json_object_is_refused(
    gateway: EventGateway, publisher, body: bytes
) -> None:
    result = gateway.handle("telegram", _telegram_headers(), body)
    assert result.status == BAD_REQUEST
    assert publisher.published == []


def test_an_authenticated_delivery_with_nothing_to_act_on_is_accepted_quietly(
    gateway: EventGateway, publisher
) -> None:
    """A delivery receipt or an empty edit is not an error; answering 4xx
    would make a well-behaved platform retry it forever."""
    result = gateway.handle("telegram", _telegram_headers(), telegram_body(text="   "))

    assert result.status == NOTHING_TO_DO
    assert result.tenant_id == ACME
    assert publisher.published == []


def test_an_oversized_body_is_refused_before_it_is_parsed(
    gateway: EventGateway, publisher
) -> None:
    result = gateway.handle("telegram", _telegram_headers(), b"x" * 2_000_000)
    assert result.status == BAD_REQUEST
    assert publisher.published == []


def test_a_named_sender_on_the_row_is_recognised_and_an_unlisted_one_is_not(
    store, secret_env, publisher
) -> None:
    """Who counts as a person on a customer's channel is a row, not a deploy.

    The channel secret proves the workspace and nothing about the human on
    the far side of it, so trust in a sender has to come from somewhere
    else. It comes from the customer's own binding row, which makes
    recognising an operator a database write that takes effect on the next
    message -- the same property that makes connecting a channel a write.
    A sender who is not on the row stays untrusted and the two-source cap
    applies to them.
    """
    from otto.ingress.store import ChannelBinding

    known_chat = 4242
    store.register(
        ChannelBinding(
            tenant_id=ACME,
            channel="telegram",
            external_id="acme-bot",
            secret_ref=ACME_TELEGRAM_REF,
            principal_allowlist={str(known_chat): "chidi"},
        ),
        credential=ACME_TELEGRAM_TOKEN,
    )
    gateway = EventGateway(
        store=store,
        secrets=EnvSecretResolver(environ=secret_env),
        publisher=publisher,
        obs=instrument("ingress"),
    )

    known = gateway.handle(
        "telegram", _telegram_headers(), telegram_body("status", chat_id=known_chat)
    )
    stranger = gateway.handle(
        "telegram", _telegram_headers(), telegram_body("status", chat_id=9999)
    )
    assert known.status == ACCEPTED
    assert stranger.status == ACCEPTED

    by_id = {e.task_id: e for e in publisher.published}
    recognised = by_id[known.task_id]
    unlisted = by_id[stranger.task_id]
    assert "principal:chidi" in recognised.provenance
    assert TrustTag.untrusted not in recognised.taint
    assert "principal:unknown" in unlisted.provenance
    assert TrustTag.untrusted in unlisted.taint
    # The reply address is minted either way: an unrecognised sender is
    # still answered, at a lower tier, rather than ignored.
    assert recognised.reply_to == str(known_chat)
    assert unlisted.reply_to == "9999"
