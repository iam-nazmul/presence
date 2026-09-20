"""Turning what people send into something the model can read.

One place, for every surface: a PDF is a PDF whether it came off WhatsApp,
Telegram or the web panel, and an adapter that had to know how to parse one
would be the third copy of this code.

Images are the exception and stay untouched -- they go to the model as pixels,
which is the whole point of a vision model, and prompts._user_content inlines
them.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

from presence.core.envelope import Attachment, Envelope
from presence.media.audio import transcribe
from presence.media.documents import Extracted, extract

__all__ = ["Extracted", "extract", "read_attachments", "transcribe"]

READABLE = ("file", "audio")


async def read_attachments(env: Envelope) -> Envelope:
    """Fill in the text of every file and voice note on this message.

    Called from the worker, not the router: pulling apart a sixty-page PDF or
    sending a voice note to a transcriber takes seconds, and ingest has to hand
    the surface back its ACK long before that.
    """
    pending = [a for a in env.attachments
               if a.data and a.kind in READABLE and not (a.text or a.problem)]
    if not pending:
        return env

    found = await asyncio.gather(*(_read(a) for a in pending))
    filled = {id(a): f for a, f in zip(pending, found, strict=True)}
    return replace(env, attachments=[
        replace(a, text=filled[id(a)].text or None, problem=filled[id(a)].problem or None)
        if id(a) in filled else a
        for a in env.attachments
    ])


async def _read(a: Attachment) -> Extracted:
    if a.kind == "audio":
        return await transcribe(a.data or b"", a.mime, a.name)
    # pypdf is CPU-bound and the loop is also holding a live WhatsApp socket.
    return await asyncio.to_thread(extract, a.data or b"", a.mime, a.name)
