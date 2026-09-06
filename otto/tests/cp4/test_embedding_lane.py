"""The vector arm's one wire: the estate router's `embed` lane.

Until 2026-09-05 this provider existed and nothing set its variables, so
every recall ran the lexical arm alone. The gateway now sets them
(idp platform/otto-gateway/deployment.yaml) and points them at a lane that
answers at 1536 dimensions, which is the width the fact table's vector
column is created at.

The width is the whole risk. A lane that quietly answers at a different
one produces vectors Postgres refuses on insert and refuses again in the
`<=>` comparison -- an error on every write and every read, discovered in
production. So these scenarios are about the number, and about the fact
that getting it wrong still costs no sender their answer.

A real HTTP server on a loopback port, not a patched `urlopen`: the thing
under test is a request on a wire, including the header the key rides in.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from otto.memory import fast_recall, store
from otto.memory.config import MemoryConfig
from otto.memory.embeddings import EmbeddingUnavailableError
from otto.memory.embeddings_litellm import (
    API_KEY_ENV,
    MODEL_ENV,
    URL_ENV,
    LiteLLMEmbeddingProvider,
    provider_from_env,
)
from otto.memory.models import Fact, Provenance

pytestmark = pytest.mark.cp4


class _Lane:
    """A stand-in for the router's embeddings endpoint.

    ``width`` is what it answers with regardless of what was asked for, so
    a lane that ignores the `dimensions` parameter can be played exactly.
    """

    def __init__(self, width: int, *, honour_request: bool = True) -> None:
        self.width = width
        self.honour_request = honour_request
        self.requests: list[dict] = []
        self.headers: list[dict] = []
        lane = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's name
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                lane.requests.append(body)
                lane.headers.append(dict(self.headers))
                width = lane.width
                if lane.honour_request and body.get("dimensions"):
                    width = int(body["dimensions"])
                payload = json.dumps({"data": [{"embedding": [0.01] * width}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args: object) -> None:
                """Silence the default stderr access log."""

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/v1"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


@pytest.fixture
def lane():
    made: list[_Lane] = []

    def make(width: int, *, honour_request: bool = True) -> _Lane:
        made.append(_Lane(width, honour_request=honour_request))
        return made[-1]

    yield make
    for one in made:
        one.close()


def test_the_call_asks_for_the_width_the_store_was_built_at(lane):
    """The dimension is not left to the router's config to remember. Every
    request names it, because the column it is going into was created from
    the same number in the same MemoryConfig."""
    router = lane(width=99)  # ignored: the request's own width wins

    provider = LiteLLMEmbeddingProvider(
        url=router.base_url, model="embed", dimensions=1536, timeout_s=5
    )
    vector = provider.embed("the founder's cluster is called estate")

    assert router.requests[0]["dimensions"] == 1536
    assert router.requests[0]["model"] == "embed"
    assert len(vector) == 1536


def test_a_lane_that_answers_at_the_wrong_width_is_refused_not_stored(lane):
    """pgvector's column is a fixed width. A 3072-dimension answer against
    a 1536 column is an insert error on every write and a query error on
    every read; caught here it is one message naming both numbers."""
    router = lane(width=3072, honour_request=False)

    provider = LiteLLMEmbeddingProvider(
        url=router.base_url, model="embed", dimensions=1536, timeout_s=5
    )
    with pytest.raises(EmbeddingUnavailableError) as raised:
        provider.embed("anything at all")

    message = str(raised.value)
    assert "3072" in message and "1536" in message, message


def test_the_key_rides_in_the_header_and_not_in_the_body(lane):
    router = lane(width=8)

    provider = LiteLLMEmbeddingProvider(
        url=router.base_url,
        model="embed",
        api_key="sk-not-a-real-key",
        dimensions=8,
        timeout_s=5,
    )
    provider.embed("hello")

    assert router.headers[0]["Authorization"] == "Bearer sk-not-a-real-key"
    assert "sk-not-a-real-key" not in json.dumps(router.requests[0])


def test_the_provider_takes_its_width_from_the_one_config_field(monkeypatch, lane):
    """Not a second copy of 1536. The same MemoryConfig field the migration
    templates otto_facts.embedding with is the one the request carries."""
    router = lane(width=0)

    monkeypatch.setenv(URL_ENV, router.base_url)
    monkeypatch.setenv(MODEL_ENV, "embed")
    provider = provider_from_env(MemoryConfig(embedding_dim=512))
    assert provider is not None
    assert len(provider.embed("a note")) == 512
    assert router.requests[0]["dimensions"] == 512


def test_an_endpoint_that_is_not_http_never_reaches_urllib(monkeypatch, caplog):
    """`file:` would have been opened as a local read. It is refused at
    construction, and the answering path gets `None` rather than an
    exception -- the same lexical-only mode as an unset variable."""
    monkeypatch.setenv(URL_ENV, "file:///etc/passwd")
    monkeypatch.setenv(MODEL_ENV, "embed")

    assert provider_from_env() is None
    assert "misconfigured" in caplog.text.lower()


def test_unset_variables_are_a_mode_and_not_a_failure(monkeypatch):
    monkeypatch.delenv(URL_ENV, raising=False)
    monkeypatch.delenv(MODEL_ENV, raising=False)
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    assert provider_from_env() is None


def test_a_router_that_refuses_costs_the_sender_nothing(
    monkeypatch, lane, db_conn, memory_config
):
    """The end-to-end shape of the risk this whole change carries: the
    embedding lane is now on the read path, and if it is down the answer
    still arrives -- from the lexical arm, over the same rows."""
    router = lane(width=8)
    base = router.base_url
    router.close()  # nothing is listening on that port any more

    store.write_fact(
        db_conn,
        Fact(
            content="the founder's cluster is called estate",
            provenance=Provenance(
                source_envelope_ulid="01M1RD03B7MEB3Q2KSS315E0ZQ",
                tier_at_capture="T2",
                taint=False,
            ),
        ),
    )
    monkeypatch.setenv(URL_ENV, base)
    monkeypatch.setenv(MODEL_ENV, "embed")

    recalled = fast_recall.recall("what is the cluster called", config=memory_config)

    assert "estate" in recalled, recalled
