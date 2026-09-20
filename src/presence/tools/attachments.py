"""Reaching back for a file that arrived a few messages ago.

Whatever someone sends is read into the turn it arrived in, and then it is gone
from the history -- sixty pages cannot ride along on every subsequent message.
But people send the file first and ask the question afterwards, so the text has
to be reachable again, and the bytes are already parked in uploads by the
router. This is the way back to them.
"""

from __future__ import annotations

import asyncio

from presence.media import extract, transcribe
from presence.store import db
from presence.tools.registry import ToolContext, tool

# Long enough to cover "here you go" followed by a question a few minutes later,
# short enough that yesterday's contract is not what gets answered about today.
WITHIN_MINUTES = 120


@tool(risk="read", scopes={"context"})
async def read_attachment(ctx: ToolContext, kind: str = "file") -> str:
    """Read the most recent file ("file") or voice note ("audio") sent in this
    conversation, again.

    The contents of anything sent are already in the message it came with. Use
    this when someone asks about a document or a recording from an earlier
    message, instead of asking them to send it a second time.
    """
    kind = "audio" if kind.strip().lower() in ("audio", "voice", "voice note") else "file"
    found = db.latest_upload(ctx.conv_key, kind, within_minutes=WITHIN_MINUTES)
    if found is None:
        return (f"No {'voice note' if kind == 'audio' else 'file'} has arrived in this "
                f"conversation in the last {WITHIN_MINUTES} minutes.")

    data, mime = found
    if kind == "audio":
        read = await transcribe(data, mime)
    else:
        # pypdf is CPU-bound, and this loop is also holding live sockets.
        read = await asyncio.to_thread(extract, data, mime)
    if read.text:
        label = "They said" if kind == "audio" else "The file says"
        return f"{label}:\n{read.text}"
    return f"That one could not be read: {read.problem}."
