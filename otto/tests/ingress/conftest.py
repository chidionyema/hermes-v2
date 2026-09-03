"""Shared fixtures for the Universal Event Gateway tests.

Same observability idiom as the boot suite: ``OTTO_OBS_MODE=test`` binds
in-memory exporters, and the shared store is reset either side of every
test so one test's ``shutdown()`` cannot poison the next.

The pieces a gateway needs are assembled here once so each test states
only what it is actually about.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from otto.ingress.gateway import EventGateway
from otto.ingress.secrets import EnvSecretResolver
from otto.ingress.store import ChannelBinding, SqliteChannelBindingStore
from otto.obs import instrument
from otto.obs.config import MODE_ENV, MODE_TEST
from otto.obs.export import obs_test_store
from otto.spine.envelope import TaskEnvelope

#: Two customers, so every routing assertion is about telling them apart
#: rather than about one customer working at all.
ACME = "tenant-acme"
GLOBEX = "tenant-globex"

ACME_TELEGRAM_TOKEN = "acme-telegram-secret-token"  # noqa: S105 - test fixture
GLOBEX_TELEGRAM_TOKEN = "globex-telegram-secret-token"  # noqa: S105 - test fixture
ACME_HTTP_TOKEN = "acme-http-bearer-token"  # noqa: S105 - test fixture

ACME_TELEGRAM_REF = "vault://otto/acme/telegram"
GLOBEX_TELEGRAM_REF = "vault://otto/globex/telegram"
ACME_HTTP_REF = "vault://otto/acme/http"


def _reset_shared_store() -> None:
    store = obs_test_store()
    store.clear()
    store.span_exporter = InMemorySpanExporter()


@pytest.fixture(autouse=True)
def _obs_test_mode(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(MODE_ENV, MODE_TEST)
    _reset_shared_store()
    yield
    _reset_shared_store()


@dataclass
class RecordingPublisher:
    """Stands in for JetStream. Records the exact envelope the gateway
    would have published, which is what every assertion here is about;
    the bus itself has its own suite in cp1."""

    published: list[TaskEnvelope] = field(default_factory=list)

    def publish_submitted(self, envelope: TaskEnvelope) -> str:
        self.published.append(envelope)
        return "otto.task.v1.submitted"

    @property
    def last(self) -> TaskEnvelope:
        return self.published[-1]


@pytest.fixture
def store() -> SqliteChannelBindingStore:
    """A binding store holding one customer on Telegram and the same
    customer on the generic HTTP channel. The second customer is
    deliberately absent: tests that need it register it themselves, at
    runtime, which is the point being proved."""
    store = SqliteChannelBindingStore()
    store.register(
        ChannelBinding(
            tenant_id=ACME,
            channel="telegram",
            external_id="acme-bot",
            secret_ref=ACME_TELEGRAM_REF,
        ),
        credential=ACME_TELEGRAM_TOKEN,
    )
    store.register(
        ChannelBinding(
            tenant_id=ACME,
            channel="http",
            external_id="acme-companion-app",
            secret_ref=ACME_HTTP_REF,
        ),
        credential=ACME_HTTP_TOKEN,
    )
    yield store
    store.close()


@pytest.fixture
def secret_env() -> dict[str, str]:
    """The projected secret values, keyed the way ``EnvSecretResolver``
    derives them from a reference."""
    from otto.ingress.secrets import env_var_name

    return {
        env_var_name(ACME_TELEGRAM_REF): ACME_TELEGRAM_TOKEN,
        env_var_name(GLOBEX_TELEGRAM_REF): GLOBEX_TELEGRAM_TOKEN,
        env_var_name(ACME_HTTP_REF): ACME_HTTP_TOKEN,
    }


@pytest.fixture
def publisher() -> RecordingPublisher:
    return RecordingPublisher()


@pytest.fixture
def gateway(store, secret_env, publisher) -> EventGateway:
    return EventGateway(
        store=store,
        secrets=EnvSecretResolver(environ=secret_env),
        publisher=publisher,
        obs=instrument("ingress"),
    )


def telegram_body(text: str = "hello", chat_id: int = 4242) -> bytes:
    return json.dumps(
        {"message": {"chat": {"id": chat_id}, "text": text, "date": 1_700_000_000}}
    ).encode("utf-8")


def http_body(text: str = "hello", caller_id: str = "app-user") -> bytes:
    return json.dumps({"caller_id": caller_id, "content": text}).encode("utf-8")
