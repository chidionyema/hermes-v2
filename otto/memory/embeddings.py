"""Pluggable embedding provider interface.

LAW 34 (provider agnostic from day 0): the memory-engine core defines a
shape, not a vendor. No provider SDK (OpenAI, Anthropic, Voyage, ...) is
imported here. A concrete production provider is wired up outside this
package (the gateway/spine lanes' concern); this module only ships the
interface plus deterministic test doubles used by CP4's own tests.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


class EmbeddingUnavailableError(RuntimeError):
    """Raised by a provider that cannot currently produce an embedding
    (down, rate-limited, or degraded past its own patience). Retrieval
    treats this the same as a timeout: fall back to lexical search."""


@runtime_checkable
class EmbeddingProvider(Protocol):
    """The one method retrieval needs. Any concrete provider - hosted or
    local, any vendor - implements this and nothing else is assumed."""

    def embed(self, text: str) -> list[float]:
        """Return the embedding for ``text``.

        Raises ``EmbeddingUnavailableError`` (or lets a ``TimeoutError``
        propagate) when it cannot produce one; the caller decides what
        "degraded" means in terms of a deadline, not this method.
        """
        ...


class NullEmbeddingProvider:
    """No embedding provider configured at all. Used to prove retrieval's
    fallback path when there is nothing to fall back *from*."""

    def embed(self, text: str) -> list[float]:
        raise EmbeddingUnavailableError("no embedding provider configured")


class DegradedEmbeddingProvider:
    """Simulates a hosted embedding API that is unreachable or too slow -
    the cp4 feature's degradation scenario. ``delay_s`` models latency;
    set it above a caller's deadline to force a timeout-driven fallback."""

    def __init__(self, delay_s: float = 0.0, raises: bool = True) -> None:
        self._delay_s = delay_s
        self._raises = raises

    def embed(self, text: str) -> list[float]:
        import time

        if self._delay_s:
            time.sleep(self._delay_s)
        if self._raises:
            raise EmbeddingUnavailableError("embedding provider degraded")
        return [0.0]


class FixedEmbeddingProvider:
    """A deterministic, healthy provider for tests: same text -> same
    vector, via a stable hash so no external call is ever made."""

    def __init__(self, dim: int) -> None:
        self._dim = dim

    def embed(self, text: str) -> list[float]:
        import hashlib

        digest = hashlib.sha256(text.encode("utf-8")).digest()
        # Repeat/trim the digest bytes to the configured dimension and
        # scale to [-1, 1]; deterministic, no network, no vendor code.
        raw = (digest * ((self._dim // len(digest)) + 1))[: self._dim]
        return [(b / 127.5) - 1.0 for b in raw]
