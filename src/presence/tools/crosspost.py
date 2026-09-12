"""Cross-surface delivery. The agent can reach you somewhere other than here."""

from __future__ import annotations

from presence.core.envelope import Conversation
from presence.core.reply import Reply
from presence.gateway import hub
from presence.store import db
from presence.tools.registry import ToolContext, tool


@tool(risk="external", scopes={"crosspost"},
      confirm="Send this to the user on {surface}?")
async def send_to(ctx: ToolContext, surface: str, text: str) -> str:
    """Send this person a message on a different surface from the one you are
    replying on -- for example, answer here in the terminal but also push the
    summary to their phone.

    surface must be one of the live surfaces; call list_surfaces if unsure.
    Use this only when the user asked to be reached elsewhere.
    """
    surface = (surface or "").strip().lower()
    if surface not in hub.live_surfaces():
        return (f"'{surface}' is not running right now. Live surfaces: "
                f"{', '.join(hub.live_surfaces()) or 'none'}.")

    row = db.conn().execute(
        "SELECT key FROM conversations WHERE principal_id = ? AND surface = ?"
        " ORDER BY last_seen_at DESC LIMIT 1",
        (ctx.principal_id, surface),
    ).fetchone()
    if row is None:
        return (f"I have no {surface} conversation for this person yet. They need to "
                f"message me there once first.")

    conv = Conversation.from_key(row["key"])
    sent = await hub.deliver(conv, Reply.text(text))
    return f"Sent to {surface}." if sent else f"Delivery to {surface} failed."


@tool(risk="read", scopes={"context"})
async def list_surfaces(ctx: ToolContext) -> str:
    """List the surfaces this agent is currently reachable on."""
    live = hub.live_surfaces()
    return ("Live surfaces: " + ", ".join(live)) if live else "No surfaces are running."
