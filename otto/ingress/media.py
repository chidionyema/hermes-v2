"""Media in, words out — voice to transcript, photo to description.

Spec ``docs/specs/otto-door-hands-and-senses.md`` steps 3 and 4: Otto's
Telegram door gains ears (a voice note becomes reachable content prefixed
``[voice] `` with its transcript) and eyes (a photo becomes reachable
content prefixed ``[image] `` with its caption and a description), so the
router and memory only ever see words. ADR 0022 keeps it vendor-neutral
and local-first; the real speech and vision tools live in the fork
(``tools/transcription_tools.py``, ``tools/vision_tools.py``), which
materialises at container build and is absent from a bare checkout. Those
imports therefore happen only inside the concrete seam implementations,
never at module import, so a unit test that stubs the seam runs offline.

This module is deliberately NOT part of ``otto/surface``. A surface
binding is a pure function that must not hold a bot token, open a socket
or import a fork-only tool; ``TelegramBinding.normalize`` stays exactly
that. The bot token is resolved in the ingress gateway (``otto/ingress``)
and handed to the seam here, which is where network and the fork are
allowed to live — this is the one place an inbound update is ever
"reached into" for pixels or audio, mirroring how ``LiteLLMClient.complete``
already holds sockets and keys out of the pure router core.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

#: The content prefixes the router and memory read (spec steps 3 and 4).
VOICE_PREFIX = "[voice] "
IMAGE_PREFIX = "[image] "
#: The question handed to the vision model when a photo has no caption.
DEFAULT_DESCRIBE_QUESTION = "describe this"

#: Kinds of media ``MediaIntent.kind`` may hold.
VOICE = "voice"
AUDIO = "audio"
IMAGE = "image"


@dataclass(frozen=True)
class MediaIntent:
    """What a Telegram update asks the door to sense, decided purely off
    the JSON the webhook already hands us — no fetch here, ever.

    ``file_id`` is the Telegram media id a fetch step later resolves
    through ``getFile``. ``caption`` is a photo's caption when the sender
    wrote one. A plain text message yields ``MediaIntent(None)``.
    """

    kind: str | None
    file_id: str | None = None
    caption: str | None = None

    @property
    def is_media(self) -> bool:
        return self.kind is not None

    @property
    def is_speech(self) -> bool:
        """A voice note or an audio document is speech: it is transcribed
        (step 3) and its sender expects an audible reply (ADR 0022)."""
        return self.kind in (VOICE, AUDIO)


@dataclass(frozen=True)
class MediaContent:
    """The words a media update became, and how to answer it.

    ``content`` fixes the envelope's reachable text: ``[voice] <transcript>``
    or ``[image] <caption>\\n<description>``. ``wants_voice_reply`` tells
    the answering lane that this inbound was speech, so its reply is sent
    as audio as well as text (ADR 0022: voice replies on).
    """

    content: str
    wants_voice_reply: bool = False


class VoiceTranscriber(Protocol):
    """Speech-to-text. The concrete edge is the fork's
    ``tools.transcription_tools`` (local faster-whisper first, estate
    router fallback — ADR 0022); a test injects a stub."""

    def transcribe(self, audio_path: Path) -> str:
        """Return the transcript of the audio at ``audio_path``."""
        ...


class ImageDescriber(Protocol):
    """Vision. The concrete edge is the fork's ``tools.vision_tools`` via
    the ``gemini`` alias; a test injects a stub."""

    def describe(self, image_path: Path, question: str) -> str:
        """Return a description of the image at ``image_path``."""

        ...


class TelegramFileFetcher(Protocol):
    """Fetches a Telegram media file id to a local path. The concrete edge
    calls Telegram's ``getFile`` then downloads the file URL, using the bot
    token; a test injects a stub that writes known bytes to ``/tmp``."""

    def download(self, bot_token: str, file_id: str, *, suffix: str) -> Path: ...


def detect_media(message: dict) -> MediaIntent:
    """Decide, purely from a parsed update ``message``, what media it asks
    the door to sense (spec steps 3 and 4). Reads only JSON fields — a
    ``voice`` or ``audio`` object carries the audio ``file_id``; a ``photo``
    array carries the photo sizes, of which the largest is fetched. An
    absent object means text, however long the update otherwise is.
    """
    voice = message.get("voice") or message.get("audio")
    if isinstance(voice, dict):
        file_id = voice.get("file_id")
        if isinstance(file_id, str) and file_id:
            return MediaIntent(
                kind=VOICE if "voice" in message else AUDIO, file_id=file_id
            )
    photo = message.get("photo")
    if isinstance(photo, list) and photo:
        largest = max(
            (
                p
                for p in photo
                if isinstance(p, dict) and isinstance(p.get("file_id"), str)
            ),
            key=lambda p: p.get("file_size", 0),
            default=None,
        )
        if largest is not None:
            caption = message.get("caption")
            return MediaIntent(
                kind=IMAGE,
                file_id=largest["file_id"],
                caption=caption if isinstance(caption, str) and caption else None,
            )
    return MediaIntent(None)


def format_voice(transcript: str) -> str:
    """Voice content marker (spec step 3): the transcript, plainly labelled
    so the model knows these are the sender's spoken words."""
    return f"{VOICE_PREFIX}{transcript}"


def format_image(caption: str | None, description: str) -> str:
    """Photo content marker (spec step 4): the caption when there is one,
    then the description, so the caption's own words and the model's reading
    of the pixels are both present."""
    if caption:
        return f"{IMAGE_PREFIX}{caption}\n{description}"
    return f"{IMAGE_PREFIX}{description}"


class MediaEnrichment(Protocol):
    """The one seam a media-capable door calls: it takes the wire update's
    ``message`` and the bot token and returns the words the router should
    read. Implementations compose a fetcher + transcriber or describer
    behind interfaces, so a test supplies fakes and no socket opens. The
    gateway holds one (optional); a door without one is a text-only door,
    exactly as before."""

    def process(self, message: dict, bot_token: str) -> MediaContent:
        """Enrich ``message`` into words. Raises when the media cannot be
        sensed; for a message ``detect_media`` classified as media this
        never returns an empty string."""
        ...


@dataclass(frozen=True)
class TelegramMediaEnrichment:
    """The concrete seam: download the Telegram media file with the bot
    token, then run it through the interpreter — fetched bytes to the fork
    tools. Import-free of the fork at module load: the transcriber and
    describer arrive already wired (``ForkTranscriber``/``ForkDescriber``
    below), so a bare-worktree test hands this a stub pair and stays
    offline.
    """

    fetcher: TelegramFileFetcher
    transcriber: VoiceTranscriber | None = None
    describer: ImageDescriber | None = None

    def process(self, message: dict, bot_token: str) -> MediaContent:
        intent = detect_media(message)
        if not intent.is_media or not intent.file_id:
            raise ValueError(
                "TelegramMediaEnrichment.process called on a non-media message"
            )
        suffix = (
            ".ogg" if intent.is_speech else (".jpg" if intent.kind is IMAGE else ".bin")
        )
        target = self.fetcher.download(bot_token, intent.file_id, suffix=suffix)
        if intent.is_speech:
            if self.transcriber is None:
                raise RuntimeError(
                    "voice media received but no transcriber is wired on the door"
                )
            transcript = self.transcriber.transcribe(target)
            return MediaContent(format_voice(transcript), wants_voice_reply=True)
        if intent.kind is not IMAGE:  # pragma: no cover - classified above
            raise RuntimeError(f"unknown media kind {intent.kind!r}")
        if self.describer is None:
            raise RuntimeError(
                "photo received but no image describer is wired on the door"
            )
        description = self.describer.describe(
            target, intent.caption or DEFAULT_DESCRIBE_QUESTION
        )
        return MediaContent(format_image(intent.caption, description))


class HttpTelegramFileFetcher:
    """The real Telegram file download: resolve the ``file_id`` to a
    ``file_path`` with ``getFile``, then GET the file from the Bot API's
    file endpoint, writing bytes to ``os.environ``-free ``fetch_root``.

    Pure stdlib ``urllib`` (this is the repo's established outbound idiom),
    token interpolated at call time into the URL and never logged.
    """

    def __init__(
        self,
        api_base: str = "https://api.telegram.org",
        fetch_root: str | None = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        import tempfile

        self._api_base = api_base.rstrip("/")
        self._root = fetch_root if fetch_root is not None else tempfile.gettempdir()
        self._timeout = timeout_seconds

    def download(self, bot_token: str, file_id: str, *, suffix: str) -> Path:
        import json as _json
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
            path = obj.get("result", {}).get("file_path") if obj.get("ok") else None
            if not isinstance(path, str):
                raise RuntimeError(
                    f"getFile refused: {obj.get('description', 'no description')!r}"
                )
            file_url = f"{self._api_base}/file/bot{bot_token}/{path}"
            with urllib.request.urlopen(file_url, timeout=self._timeout) as resp:  # noqa: S310
                bytes_ = resp.read()
            with open(fd, "wb") as handle:
                handle.write(bytes_)
            return Path(tmp)
        except Exception:
            try:
                Path(tmp).unlink(missing_ok=True)
            except OSError:
                pass
            raise


class ForkTranscriber:
    """Speech-to-text via the fork's ``tools.transcription_tools`` — local
    faster-whisper first, estate router fallback (ADR 0022). Imported only
    on first call, because the fork exists in the container image, never in
    a bare checkout; a bare-worktree test never reaches this line."""

    def transcribe(self, audio_path: Path) -> str:
        try:
            from tools import transcription_tools  # type: ignore[import-not-found]
        except Exception as exc:  # pragma: no cover - container-only
            raise RuntimeError("fork transcription tools unavailable") from exc
        return transcription_tools.transcribe_audio(str(audio_path))


class ForkDescriber:
    """Vision via the fork's ``tools.vision_tools`` (``gemini`` alias)."""

    def describe(self, image_path: Path, question: str) -> str:
        try:
            from tools import vision_tools  # type: ignore[import-not-found]
        except Exception as exc:  # pragma: no cover - container-only
            raise RuntimeError("fork vision tools unavailable") from exc
        return vision_tools.describe_image(str(image_path), question)


def default_media_enrichment() -> MediaEnrichment | None:
    """The seam a media-capable door wires at boot, or ``None`` when the
    door stays text-only. Fork tools are constructed lazily (never imported
    here). A production deployment constructs this once; tests build their
    own stub ``MediaEnrichment`` instead."""
    return TelegramMediaEnrichment(
        fetcher=HttpTelegramFileFetcher(),
        transcriber=ForkTranscriber(),
        describer=ForkDescriber(),
    )
