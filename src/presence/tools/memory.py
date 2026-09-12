"""Memory pack. What makes it one agent instead of five chatbots.

Memory is keyed to the Principal, not the conversation, so a fact written from
a terminal is readable from a phone.
"""

from __future__ import annotations

from presence.store import db
from presence.tools.registry import ToolContext, tool


@tool(risk="write", scopes={"memory"})
async def remember(ctx: ToolContext, key: str, value: str, scope: str = "principal") -> str:
    """Save a durable fact about this person so you can use it in any future
    conversation, on any surface.

    Use a short stable key ("preferred_language", "current_project") and a full
    sentence as the value. Writing the same key again replaces it.

    scope is "principal" (remember everywhere, the default and usually right)
    or "conversation" (only relevant to this one thread).
    """
    key = (key or "").strip()
    value = (value or "").strip()
    if not key or not value:
        return "Nothing saved: both key and value are required."
    if scope not in ("principal", "conversation", "workspace"):
        scope = "principal"
    db.remember(
        ctx.principal_id,
        key,
        value,
        scope=scope,
        scope_key=ctx.conv_key if scope == "conversation" else None,
        envelope_id=ctx.envelope.id,
    )
    where = "everywhere" if scope == "principal" else "in this conversation"
    return f"Saved '{key}' {where}."


@tool(risk="read", scopes={"memory"})
async def recall(ctx: ToolContext, query: str = "") -> str:
    """Search what you remember about this person across every surface.

    Leave query empty to list the most recent facts. Call this before saying you
    do not know something.
    """
    rows = db.recall(ctx.principal_id, query or None, ctx.conv_key, limit=10)
    if not rows:
        return (
            f"Nothing remembered matching '{query}'." if query
            else "Nothing remembered about this person yet."
        )
    lines = [f"- {r['key']}: {r['value']}" for r in rows]
    return "Remembered:\n" + "\n".join(lines)


@tool(risk="write", scopes={"memory"}, confirm="Forget '{key}'?")
async def forget(ctx: ToolContext, key: str) -> str:
    """Delete a remembered fact by its key. Use recall first to find the key."""
    n = db.forget(ctx.principal_id, key.strip())
    return f"Forgot '{key}'." if n else f"There was nothing remembered under '{key}'."
