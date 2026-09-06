"""The production embedding provider: the estate's own model router.

``otto/memory/embeddings.py`` deliberately ships an interface and test
doubles and no vendor code (LAW 34). This module is the one concrete
implementation, and it is still not a vendor: it speaks the OpenAI
embeddings wire format to the estate's LiteLLM proxy, which is the single
model-routing layer the platform already runs (``llm/config.yaml`` in the
idp repo). Swapping the model, or the vendor behind it, is a change to
that proxy's config and not to this file.

Everything is read from the environment (LAW 46) and nothing here has a
default host, model or credential. When the endpoint or the model is
unset, ``provider_from_env`` returns ``None`` and
``otto/memory/retrieval.py`` runs its lexical arm alone -- Postgres
full-text search over the same rows. That is a real answer, not a
failure: it is the BM25 half of the hybrid, and it is why memory keeps
working before an embedding model is wired up.

The estate wires it to the `embed` lane on its own router
(idp platform/otto-gateway/deployment.yaml), which resolves to
gemini-embedding-001 at 1536 dimensions -- the width
``MemoryConfig.embedding_dim`` templates into the fact table. Every
request asks for that width and every response is checked against it, so
a lane that silently changes shape fails loudly on the first call instead
of erroring on every insert afterwards.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request

from otto.memory.config import MemoryConfig, load_config
from otto.memory.embeddings import EmbeddingProvider, EmbeddingUnavailableError

_LOG = logging.getLogger(__name__)

#: OpenAI-compatible base, e.g. http://litellm.llm.svc:4000/v1
URL_ENV = "OTTO_MEMORY_EMBEDDING_URL"
#: A LiteLLM alias, not a vendor model name -- the proxy maps it.
MODEL_ENV = "OTTO_MEMORY_EMBEDDING_MODEL"
#: Read from the environment, injected from the estate's secret store.
#: Never written into this repo and never logged.
API_KEY_ENV = "OTTO_MEMORY_EMBEDDING_API_KEY"
#: The per-call ceiling. retrieval.py enforces its own deadline on top of
#: this from the caller's side; this one stops a socket hanging forever.
TIMEOUT_ENV = "OTTO_MEMORY_EMBEDDING_TIMEOUT_S"
DEFAULT_TIMEOUT_S = 1.5


def require_http(url: str, env_name: str) -> None:
    """Refuse anything but http(s) before it reaches urllib.

    ``urllib.request`` will happily open ``file:`` and other schemes, so a
    misconfigured environment variable could turn an endpoint setting into
    a local file read. The check is here rather than at the call site
    because it has to hold for every caller, and it is what makes the
    ``noqa: S310`` on those calls a statement rather than a silencer (the
    same convention as ``otto/memory/hindsight.py``).
    """
    scheme = urllib.parse.urlparse(url).scheme
    if scheme not in ("http", "https"):
        raise ValueError(f"{env_name} must be an http(s) URL; got scheme {scheme!r}")


class LiteLLMEmbeddingProvider:
    """An ``EmbeddingProvider`` backed by the estate's model router.

    Raises ``EmbeddingUnavailableError`` for every failure mode -- a bad
    status, a timeout, an unparseable body -- because retrieval treats
    "unavailable" and "too slow" identically and falls back to lexical
    search. Nothing here is allowed to propagate an exception shape the
    core does not know about.
    """

    def __init__(
        self,
        url: str,
        model: str,
        api_key: str | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        dimensions: int | None = None,
    ) -> None:
        require_http(url, URL_ENV)
        self._url = url.rstrip("/") + "/embeddings"
        self._model = model
        self._api_key = api_key
        self._timeout_s = timeout_s
        self._dimensions = dimensions

    def embed(self, text: str) -> list[float]:
        request: dict[str, object] = {"model": self._model, "input": text}
        if self._dimensions is not None:
            # The OpenAI embeddings parameter, which LiteLLM forwards to whichever
            # vendor is behind the lane. Asked for on every call rather than left to
            # the proxy's config, so this process gets the width its fact table was
            # created at even if the router's row is edited.
            request["dimensions"] = self._dimensions
        body = json.dumps(request).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        req = urllib.request.Request(  # noqa: S310 - require_http refused any scheme but http(s)
            self._url, data=body, headers=headers
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout_s) as resp:  # noqa: S310 - same
                payload = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            # The message never includes the request body or the key.
            raise EmbeddingUnavailableError(
                f"embedding call to the estate router failed: {type(exc).__name__}"
            ) from exc
        try:
            vector = payload["data"][0]["embedding"]
        except (KeyError, IndexError, TypeError) as exc:
            raise EmbeddingUnavailableError(
                "embedding response had no data[0].embedding"
            ) from exc
        if not isinstance(vector, list) or not vector:
            raise EmbeddingUnavailableError(
                "embedding response carried an empty vector"
            )
        if self._dimensions is not None and len(vector) != self._dimensions:
            # A vector of the wrong width cannot be stored in, or compared against,
            # a pgvector column of a fixed dimension -- Postgres refuses it. Caught
            # here it is one warning and a lexical answer; missed, it is an insert
            # error on every write and a query error on every read. The most likely
            # cause is a router lane that ignored `dimensions`, and this message is
            # what names it.
            raise EmbeddingUnavailableError(
                f"embedding lane {self._model!r} returned {len(vector)} dimensions, "
                f"but this store is built for {self._dimensions}"
            )
        return [float(x) for x in vector]


def provider_from_env(config: MemoryConfig | None = None) -> EmbeddingProvider | None:
    """The provider this process should use, or ``None`` when no endpoint
    and model are configured.

    ``None`` is a supported, tested state, not an error: retrieval falls
    back to Postgres full-text search over the same facts. Returning a
    provider that cannot work would instead cost every recall the
    embedding deadline before falling back to exactly the same place.
    """
    url = os.environ.get(URL_ENV)
    model = os.environ.get(MODEL_ENV)
    if not url or not model:
        return None
    try:
        return _build(url, model, config)
    except (ValueError, TypeError):
        # A malformed endpoint or timeout is a configuration defect, and it is
        # loud in the log -- but it is not worth an exception on the answering
        # path. Every caller here already treats "no provider" as the lexical-only
        # mode, so the degradation is one that is tested rather than a new one.
        _LOG.error(
            "embedding provider is misconfigured; recall will use full-text search "
            "alone. Check %s and %s.",
            URL_ENV,
            TIMEOUT_ENV,
            exc_info=True,
        )
        return None


def _build(url: str, model: str, config: MemoryConfig | None) -> EmbeddingProvider:
    raw_timeout = os.environ.get(TIMEOUT_ENV)
    timeout = float(raw_timeout) if raw_timeout else DEFAULT_TIMEOUT_S
    config = config or load_config()
    return LiteLLMEmbeddingProvider(
        url=url,
        model=model,
        api_key=os.environ.get(API_KEY_ENV),
        timeout_s=timeout,
        # One number, read from one place: the same MemoryConfig field the migration
        # templates otto_facts.embedding with. The column and the request cannot
        # disagree because neither of them carries its own copy of the width.
        dimensions=config.embedding_dim,
    )
