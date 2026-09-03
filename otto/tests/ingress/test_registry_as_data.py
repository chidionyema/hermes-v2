"""Onboarding a customer is a database write, not a deployment.

This is the whole point of the gateway. The founder's directive of
2026-09-03: connecting a customer's channel must be "a database-driven
event" — no command line, no manifest edit, no pipeline run, no pod
restart. The test below proves that literally: one gateway object is
built, a customer it has never heard of is added while it is running, and
their next message routes to them.

The proof that no restart happened is that the same ``gateway`` object,
with the same identity, answers both before and after — a restart would
mean a new object.
"""

from __future__ import annotations

from otto.ingress.gateway import ACCEPTED, UNAUTHORIZED, EventGateway
from otto.ingress.store import ChannelBinding

from .conftest import (
    ACME,
    GLOBEX,
    GLOBEX_TELEGRAM_REF,
    GLOBEX_TELEGRAM_TOKEN,
    telegram_body,
)


def _headers(token: str) -> dict[str, str]:
    return {"X-Telegram-Bot-Api-Secret-Token": token}


def test_a_second_customer_is_onboarded_while_the_gateway_is_running(
    gateway: EventGateway, store, publisher
) -> None:
    gateway_identity = id(gateway)

    before = gateway.handle(
        "telegram", _headers(GLOBEX_TELEGRAM_TOKEN), telegram_body("hello")
    )
    assert before.status == UNAUTHORIZED, "the customer does not exist yet"

    # The whole of onboarding: one row. No restart, no reload call, no
    # manifest, no pipeline.
    store.register(
        ChannelBinding(
            tenant_id=GLOBEX,
            channel="telegram",
            external_id="globex-bot",
            secret_ref=GLOBEX_TELEGRAM_REF,
        ),
        credential=GLOBEX_TELEGRAM_TOKEN,
    )

    after = gateway.handle(
        "telegram", _headers(GLOBEX_TELEGRAM_TOKEN), telegram_body("hello")
    )

    assert after.status == ACCEPTED
    assert after.tenant_id == GLOBEX
    assert publisher.last.tenant_id == GLOBEX
    assert id(gateway) == gateway_identity, (
        "the same running gateway served both requests; if this ever fails "
        "the test has been rewritten to restart something, which is the "
        "thing it exists to forbid"
    )


def test_the_new_customers_messages_never_land_on_the_first_customer(
    gateway: EventGateway, store, publisher
) -> None:
    store.register(
        ChannelBinding(
            tenant_id=GLOBEX,
            channel="telegram",
            external_id="globex-bot",
            secret_ref=GLOBEX_TELEGRAM_REF,
        ),
        credential=GLOBEX_TELEGRAM_TOKEN,
    )

    from .conftest import ACME_TELEGRAM_TOKEN

    gateway.handle("telegram", _headers(ACME_TELEGRAM_TOKEN), telegram_body("first"))
    gateway.handle("telegram", _headers(GLOBEX_TELEGRAM_TOKEN), telegram_body("second"))

    tenants = [envelope.tenant_id for envelope in publisher.published]
    assert tenants == [ACME, GLOBEX]
    inputs = {envelope.tenant_id: envelope.input for envelope in publisher.published}
    assert inputs == {ACME: "first", GLOBEX: "second"}


def test_disconnecting_a_customer_is_also_one_write(
    gateway: EventGateway, store, publisher
) -> None:
    """The reverse of onboarding has to be as cheap, or the platform grows
    a second, slower path for turning a customer off."""
    from otto.ingress.store import DISABLED

    from .conftest import ACME_TELEGRAM_TOKEN

    assert (
        gateway.handle(
            "telegram", _headers(ACME_TELEGRAM_TOKEN), telegram_body()
        ).status
        == ACCEPTED
    )

    store.set_status("telegram", "acme-bot", DISABLED)

    assert (
        gateway.handle(
            "telegram", _headers(ACME_TELEGRAM_TOKEN), telegram_body()
        ).status
        != ACCEPTED
    )
    assert len(publisher.published) == 1
