"""Voice notes in, words out.

A voice note is not a file someone attached, it is what they said -- on WhatsApp
it is often the whole message. Answering "I cannot listen to audio" to a
fifteen-second question is the same failure as refusing to open a PDF.

Transcription needs a model that does audio, and the local Ollama default does
not, so this talks to any OpenAI-compatible /audio/transcriptions endpoint:
OpenAI itself, Groq, or a whisper.cpp / speaches server on the same machine.
STT_BASE_URL points at one. With nothing configured the agent says it could not
hear the note and asks them to type it -- which is honest, and is the one thing
it must never do while a transcript is sitting in front of it.
"""

from __future__ import annotations

import logging

import httpx

from presence.config import settings
from presence.media.documents import Extracted

log = logging.getLogger("presence.media")

# What the hosted endpoints accept, and roughly what a phone will send anyway.
MAX_AUDIO_BYTES = 24 * 1024 * 1024

# Whisper decides how to decode from the filename, so a voice note posted as
# "file" comes back empty or as garbage. WhatsApp PTT is opus in an ogg
# container; Telegram's is the same.
EXTENSIONS = {
    "audio/ogg": ".ogg",
    "audio/opus": ".ogg",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/aac": ".m4a",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/webm": ".webm",
    "audio/flac": ".flac",
}

NOT_CONFIGURED = ("there is no transcription set up here, so the voice note "
                  "could not be heard")


def endpoint() -> tuple[str, str, str] | None:
    """(base_url, api_key, model), or None when nothing can transcribe.

    The main provider only doubles as a transcriber when it actually is one:
    Ollama and OpenRouter answer the chat endpoint and 404 this one, and a 404
    that reads as "your voice note failed" would send people hunting for a bug
    in their audio.
    """
    base = settings.stt_base_url.strip()
    if base:
        return base.rstrip("/"), (settings.stt_api_key or settings.api_key), settings.stt_model
    if settings.provider == "openai" and settings.api_key not in ("", "none"):
        return settings.base_url.rstrip("/"), settings.api_key, settings.stt_model
    return None


async def transcribe(data: bytes, mime: str | None = None,
                     name: str | None = None) -> Extracted:
    """Audio bytes -> what was said. Always answers; never raises."""
    if not data:
        return Extracted(problem="the voice note arrived empty")
    if len(data) > MAX_AUDIO_BYTES:
        return Extracted(problem="the recording is too long to transcribe")

    target = endpoint()
    if target is None:
        return Extracted(problem=NOT_CONFIGURED)
    base, key, model = target

    mime = (mime or "audio/ogg").split(";")[0].strip().lower()
    filename = f"{(name or 'voice').rsplit('.', 1)[0]}{EXTENSIONS.get(mime, '.ogg')}"

    try:
        async with httpx.AsyncClient(timeout=120.0) as http:
            r = await http.post(
                f"{base}/audio/transcriptions",
                headers={"Authorization": f"Bearer {key}"} if key else {},
                files={"file": (filename, data, mime)},
                data={"model": model, "response_format": "json"},
            )
            r.raise_for_status()
            said = (r.json().get("text") or "").strip()
    except Exception as e:
        log.warning("transcription failed: %s", e)
        return Extracted(problem="the voice note could not be transcribed")

    if not said:
        # Silence, or a recording of a room. Worth distinguishing: the answer to
        # this is "I couldn't make anything out", not "send it again".
        return Extracted(problem="the voice note came back silent")
    return Extracted(text=said)
