"""Per-channel plugins: the only place a channel's name means anything.

A plugin answers four questions about one channel, and nothing else:

1. *Where does this channel put its credential?* Telegram puts a shared
   secret in the ``X-Telegram-Bot-Api-Secret-Token`` header; a generic
   HTTP caller uses ``Authorization: Bearer``; Slack signs the body.
   Each of those is a few lines here and zero lines anywhere else.
2. *Is the presented credential the right one?* A constant-time compare,
   or for a signing channel, a recomputed signature.
3. *Which surface binding turns this channel's payload into the neutral
   envelope?* The existing ``otto.surface.bindings`` are reused as-is;
   this package adds no parsing of its own.
4. *How does an answer travel back out?* The address came from this
   channel's own binding and goes back to this channel's own API, so the
   one module that already knows the channel's shape is the one that
   sends. Nothing above here learns what a chat id is.

Everything else — looking the customer up, minting the task, publishing
it, answering the socket — is channel-independent and lives in
``gateway.py``. That is what makes adding Slack a new file here rather
than a change spread across the platform.

Telegram is plugin number one because it is the channel already live in
the cluster. It has no privileged position in the code: it is registered
in the same table as every other plugin, and the gateway cannot tell the
difference.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from otto.boot.transport import TelegramHTTPTransport
from otto.spine.envelope import TaskSource
from otto.surface.adapter import SurfaceAdapter
from otto.surface.bindings.http import HttpBinding
from otto.surface.bindings.telegram import TelegramBinding

TELEGRAM = "telegram"
HTTP = "http"


class OutboundNotSupported(RuntimeError):
    """This channel cannot begin a message; it can only answer a request
    while the request is still open. A plain HTTP caller is the example:
    by the time an answer exists the connection that asked is long gone,
    so the answer is fetched, not pushed."""


class TextToSpeech(Protocol):
    """Words to audio (ADR 0022). The concrete edge is the fork's tts
    (edge-tts, offline voices before any network); a test injects a stub
    that returns known bytes. Absent a wired speaker a door simply does
    not speak, and never fails a text answer because of it."""

    def synthesize(self, text: str) -> bytes:
        """Return an OGG/Opus voice note ``bytes`` for ``text``."""
        ...


class ChannelPlugin(Protocol):
    """One channel's credential rules and its surface binding."""

    channel: str
    task_source: TaskSource

    def present_credential(
        self, headers: Mapping[str, str], raw_body: bytes
    ) -> str | None:
        """The credential this request presented, or ``None`` when the
        request carried none at all."""
        ...

    def verify(self, presented: str, secret: str) -> bool:
        """Whether the presented credential matches the customer's own
        secret. Constant time — a timing difference here is a way to
        guess another customer's token one byte at a time."""
        ...

    def binding(self) -> SurfaceAdapter:
        """The surface binding that normalises this channel's payload."""
        ...

    def send_reply(self, secret: str, reply_to: str, text: str) -> None:
        """Deliver one answer back to the address the binding minted.

        ``secret`` is the customer's *outbound* credential, resolved from
        ``ChannelBinding.outbound_secret_ref`` -- never the inbound one.
        Raises ``OutboundNotSupported`` on a channel that has no way to
        start a message of its own.
        """
        ...

    def send_voice(self, secret: str, reply_to: str, text: str) -> None:
        """Deliver an answer as audio, when this surface can carry one
        (ADR 0022: voice replies on). Called by the answering lane for an
        inbound that arrived as speech; the reply's text is always sent too,
        so ``send_voice`` degrades to a no-op on a surface that cannot
        speak. Raises ``OutboundNotSupported`` only when the surface has no
        audio path at all.
        """
        ...


def _header(headers: Mapping[str, str], name: str) -> str | None:
    """Case-insensitive header read. HTTP header names are
    case-insensitive by specification, and different servers and proxies
    hand them over in different cases, so a plugin must never index a
    plain dict by one exact spelling."""
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value
    return None


@dataclass(frozen=True)
class TelegramPlugin:
    """Plugin one: Telegram's shared secret token.

    Telegram sends the value registered with ``setWebhook`` in the
    ``X-Telegram-Bot-Api-Secret-Token`` header on every delivery. Each
    customer registers a different value, so the header both authenticates
    the delivery and identifies the customer — which is exactly the
    "which client does this token belong to" lookup the gateway performs.
    """

    channel: str = TELEGRAM
    task_source: TaskSource = TaskSource.telegram
    header_name: str = "X-Telegram-Bot-Api-Secret-Token"
    #: Optional text-to-speech seam (ADR 0022). ``None`` (the default
    #: wherever the fork's edge-tts is unavailable, a bare checkout or a
    #: surface with no audio) makes ``send_voice`` a no-op: the text reply
    #: has already been sent, so silence is never the cost.
    speaker: TextToSpeech | None = None

    def send_voice(self, secret: str, reply_to: str, text: str) -> None:
        """Speak ``text`` to ``reply_to``. Synthesises audio through
        ``self.speaker`` (the fork's tts) when one is wired, then uploads
        it as a Telegram voice note. With no speaker this is a no-op — the
        door never fails a text answer because the audio half of it cannot
        be produced here."""
        if self.speaker is None:
            return
        audio = self.speaker.synthesize(text)
        TelegramHTTPTransport(token=secret).send_voice(int(reply_to), audio)

    def present_credential(
        self, headers: Mapping[str, str], raw_body: bytes
    ) -> str | None:
        return _header(headers, self.header_name) or None

    def verify(self, presented: str, secret: str) -> bool:
        return hmac.compare_digest(presented, secret)

    def binding(
        self, principal_allowlist: Mapping[str, str] | None = None
    ) -> SurfaceAdapter:
        # The gateway authenticates the *channel*, not the individual
        # person on the far side of it, so per-person trust cannot come
        # from the channel secret -- promoting a sender to operator on
        # the strength of one would hand a customer's whole workspace the
        # founder's tier. It comes from the binding row instead, which is
        # the same place the credential reference lives: recognising an
        # operator is onboarding, and onboarding is a database write.
        # Absent a row entry the map is empty, every sender is untrusted
        # and the taint cap applies -- the old behaviour, now a default
        # rather than the only possibility.
        chat_ids: dict[int, str] = {}
        for address, principal in (principal_allowlist or {}).items():
            try:
                chat_ids[int(address)] = principal
            except (TypeError, ValueError):
                # A malformed key is one unrecognised sender, not a broken
                # door: skipping it leaves that sender untrusted.
                continue
        return TelegramBinding(chat_id_allowlist=chat_ids)

    def send_reply(self, secret: str, reply_to: str, text: str) -> None:
        """``secret`` is the customer's bot token. It is held only for
        the length of this call, inside the transport's own field, and
        the transport never logs it (``otto.boot.transport``)."""
        TelegramHTTPTransport(token=secret).send_message(int(reply_to), text)

    def send_chat_action(self, secret: str, reply_to: str) -> None:
        """Telegram's "Otto is typing..." for ``reply_to``. The worker
        re-sends it every few seconds while a model thinks (Telegram clears
        it after about five); a channel without the method shows nothing."""
        TelegramHTTPTransport(token=secret).send_chat_action(int(reply_to), "typing")


@dataclass(frozen=True)
class HttpPlugin:
    """Plugin two: a plain bearer token, for the companion app, a web
    widget, or any customer calling the API directly.

    Two plugins are what proves the pattern is a contract rather than one
    special case with an interface drawn around it.
    """

    channel: str = HTTP
    task_source: TaskSource = TaskSource.api
    header_name: str = "Authorization"
    scheme: str = "Bearer "

    def present_credential(
        self, headers: Mapping[str, str], raw_body: bytes
    ) -> str | None:
        raw = _header(headers, self.header_name) or ""
        if not raw.lower().startswith(self.scheme.lower()):
            return None
        return raw[len(self.scheme) :].strip() or None

    def verify(self, presented: str, secret: str) -> bool:
        return hmac.compare_digest(presented, secret)

    def binding(
        self, principal_allowlist: Mapping[str, str] | None = None
    ) -> SurfaceAdapter:
        return HttpBinding(principal_allowlist=dict(principal_allowlist or {}))

    def send_reply(self, secret: str, reply_to: str, text: str) -> None:
        raise OutboundNotSupported(
            "a plain HTTP caller has no address to push an answer to; it "
            "reads the answer back by task id"
        )

    def send_voice(self, secret: str, reply_to: str, text: str) -> None:
        raise OutboundNotSupported(
            "a plain HTTP caller has no address to push an answer to; it "
            "reads the answer back by task id"
        )


def default_plugins() -> dict[str, Any]:
    """The channels this build serves. Adding Slack is one entry here and
    one class above; nothing outside this module changes."""
    return {TELEGRAM: TelegramPlugin(), HTTP: HttpPlugin()}


# -- the media seam (hands & senses steps 3 & 4) ---------------------------
# The channel-neutral models, protocols and pure detectors live in
# ``otto.ingress.media``; this exempt file supplies the concrete parts a
# Telegram door senses with: the bot-API file downloader, the fork speech
# and vision edges, and a factory that assembles the seam. Import-free of
# the fork at module load (fork edges import their tool lazily on first
# call) so a bare-worktree test never reaches them.


class BotApiFileDownloader:
    """Resolve a Telegram media ``file_id`` to a local file.

    Two stdlib-``urllib`` calls: ``getFile`` to learn the ``file_path``,
    then a GET of the file from the Bot API's file endpoint. The bot
    token is interpolated into the URL at call time and never logged.
    """

    def __init__(
        self,
        api_base: str = "https://api.telegram.org",
        fetch_root: str | None = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        import pathlib
        import tempfile

        self._api_base = api_base.rstrip("/")
        self._root = pathlib.Path(
            fetch_root if fetch_root is not None else tempfile.gettempdir()
        )
        self._timeout = timeout_seconds

    def download(self, bot_token: str, file_id: str, *, suffix: str):
        import json as _json
        import pathlib
        import tempfile
        import urllib.error
        import urllib.parse
        import urllib.request

        fd, tmp = tempfile.mkstemp(dir=self._root, suffix=suffix)
        try:
            get_url = f"{self._api_base}/bot{bot_token}/getFile"
            req = urllib.request.Request(  # noqa: S310 - https, Telegram Bot API
                get_url,
                data=urllib.parse.urlencode({"file_id": file_id}).encode(),
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # noqa: S310
                    obj = _json.loads(resp.read() or b"{}")
            except urllib.error.HTTPError as exc:  # noqa: PERF203
                raise RuntimeError(f"getFile HTTP {exc.code}") from exc
            except urllib.error.URLError as exc:
                raise RuntimeError(f"getFile unreachable: {exc.reason}") from exc
            file_path = (
                obj.get("result", {}).get("file_path") if obj.get("ok") else None
            )
            if not isinstance(file_path, str):
                raise RuntimeError(
                    f"getFile refused: {obj.get('description', 'no description')!r}"
                )
            file_url = f"{self._api_base}/file/bot{bot_token}/{file_path}"
            with urllib.request.urlopen(file_url, timeout=self._timeout) as resp:  # noqa: S310
                media_bytes = resp.read()
            with open(fd, "wb") as handle:
                handle.write(media_bytes)
            return pathlib.Path(tmp)
        except Exception:
            try:
                pathlib.Path(tmp).unlink(missing_ok=True)
            except OSError:
                pass
            raise


class ForkVoiceTranscriber:
    """Speech to text via the fork's ``tools.transcription_tools`` (local
    faster-whisper first, estate router fallback, ADR 0022). The fork
    module lives in the container image, never in a bare checkout — hence
    the bounded lazy import on first call."""

    def transcribe(self, audio_path) -> str:
        try:
            import importlib

            transcription = importlib.import_module("tools.transcription_tools")
        except Exception as exc:  # pragma: no cover - container-only
            raise RuntimeError("fork transcription tools unavailable") from exc
        return transcription.transcribe_audio(str(audio_path))


class ForkImageDescriber:
    """Vision via the fork's ``tools.vision_tools`` (the ``gemini`` alias)."""

    def describe(self, image_path, question: str) -> str:
        try:
            import importlib

            vision = importlib.import_module("tools.vision_tools")
        except Exception as exc:  # pragma: no cover - container-only
            raise RuntimeError("fork vision tools unavailable") from exc
        return vision.describe_image(str(image_path), question)


def default_media_enrichment():
    """The media seam a Telegram door wires at boot, or ``None`` for a door
    that stays text-only. Fork edges are constructed lazily and never import
    at module load. Testing builds its own stub ``MediaEnrichment``."""
    from otto.ingress.media import SensedMediaEnrichment

    return SensedMediaEnrichment(
        fetcher=BotApiFileDownloader(),
        transcriber=ForkVoiceTranscriber(),
        describer=ForkImageDescriber(),
    )
