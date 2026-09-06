"""Media in, words out — voice to transcript, photo to description.

Spec ``docs/specs/otto-door-hands-and-senses.md`` steps 3 and 4: Otto's
door becomes reachable content out of a voice note (``[voice] says-words``)
and out of a photo (``[image] caption-or-description``). The channel-neutral
core lives here so the answering lanes never touch a channel's byte
format; the *concrete* fetcher and interpreter for a named channel live in
``otto.ingress.plugins`` (the one file the tenancy rail allows a channel to
name), because this module is compute and a compute lane may not know what
a Telegram is.

Everything in this module is pure and import-safe:

* it imports no third party, no fork tool and no ``tools`` package (the
  fork exists only in the container image), and
* ``detect_media`` reads only the JSON a webhook already handed us — no
  socket ever opens in here.

A media-capable door wires a ``MediaEnrichment`` (from plugins) into the
gateway; a door that wires none is a text-only door exactly as before.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

#: Content prefixes the router and memory read (spec steps 3 and 4).
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
    """What an update asks the door to sense, decided purely off the JSON
    the webhook hands us — no fetch here, ever.

    ``file_id`` is the channel's media id a fetch step later resolves.
    ``caption`` is a photo's caption when the sender wrote one. A plain
    text message yields ``MediaIntent(None)``.
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
        (step 3) and its sender expects an audible reply."""
        return self.kind in (VOICE, AUDIO)


@dataclass(frozen=True)
class MediaContent:
    """The words a media update became, and how to answer it.

    ``content`` fixes the envelope's reachable text: ``[voice] <transcript>``
    or ``[image] <caption>\\n<description>``. ``wants_voice_reply`` tells
    the answering lane that this inbound was speech, so its reply is sent
    as audio as well as text.
    """

    content: str
    wants_voice_reply: bool = False


class VoiceTranscriber(Protocol):
    """Speech to text. The concrete edge is the fork's
    ``tools.transcription_tools`` (local first, estate router fallback); a
    test injects a stub."""

    def transcribe(self, audio_path: Path) -> str:
        """Return the transcript of the audio at ``audio_path``."""
        ...


class ImageDescriber(Protocol):
    """Vision. The concrete edge is the fork's ``tools.vision_tools``; a
    test injects a stub."""

    def describe(self, image_path: Path, question: str) -> str:
        """Return a description of the image at ``image_path``."""
        ...


class MediaFileFetcher(Protocol):
    """Fetches a channel's media file id to a local path. The concrete edge
    calls that channel's ``getFile``/download API with its bot token; a
    test injects a stub that writes known bytes to a temp dir."""

    def download(self, bot_token: str, file_id: str, *, suffix: str) -> Path: ...


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
class SensedMediaEnrichment:
    """A concrete ``MediaEnrichment`` built from parts: download the
    media file (``fetcher``, typically a channel's bot-API downloader from
    ``otto.ingress.plugins``), then run it through the transcriber or
    describer seam. Channel-agnostic by construction — the parts are
    injected, so no socket and no channel name live in compute.

    The transcriber/describer arrive already wired (the fork edges live in
    ``otto.ingress.plugins`` too), so a bare-worktree test hands this a
    stub pair and stays offline.
    """

    fetcher: MediaFileFetcher
    transcriber: VoiceTranscriber | None = None
    describer: ImageDescriber | None = None

    def process(self, message: dict, bot_token: str) -> MediaContent:
        intent = detect_media(message)
        if not intent.is_media or not intent.file_id:
            raise ValueError(
                "SensedMediaEnrichment.process called on a non-media message"
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


def detect_media(message: dict) -> MediaIntent:
    """Decide, purely from a parsed update ``message``, what media it asks
    the door to sense (spec steps 3 and 4). Reads only JSON fields — a
    ``voice`` or ``audio`` object carries the audio ``file_id``; a ``photo``
    array carries sizes, of which the largest is fetched. An absent object
    means text, however long the update otherwise is.
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
    then the description, so the caption's own words and the model's
    reading of the pixels are both present."""
    if caption:
        return f"{IMAGE_PREFIX}{caption}\n{description}"
    return f"{IMAGE_PREFIX}{description}"
