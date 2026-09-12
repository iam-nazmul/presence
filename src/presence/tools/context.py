"""Context pack. How the agent asks the surface what is on the user's screen.

SurfaceContext is already rendered into the system prompt, so this tool exists
for the cases where the model wants a specific field verbatim -- a document id,
a full URL, a selection it must quote exactly.
"""

from __future__ import annotations

from presence.tools.registry import ToolContext, tool


@tool(risk="read", scopes={"context"})
async def read_context(ctx: ToolContext, key: str = "") -> str:
    """Read what the surface knows about where this conversation is happening --
    the page the user has open, their selection, the channel, their timezone.

    Leave key empty to list everything available. This is how you resolve words
    like "this", "here" and "the current one" without asking.
    """
    data = ctx.envelope.context.data
    if not data:
        return "This surface reported no extra context."
    if key:
        if key in data:
            return f"{key}: {data[key]}"
        return f"No '{key}' here. Available: {', '.join(data)}."
    return "Context for this conversation:\n" + "\n".join(f"- {k}: {v}" for k, v in data.items())


@tool(risk="read", scopes={"context"})
async def whoami(ctx: ToolContext) -> str:
    """Report which surfaces this person is linked on and how much you trust
    this particular conversation. Use it when asked what you can do here."""
    from presence.store import db

    rows = db.identities_of(ctx.principal_id)
    linked = ", ".join(f"{r['surface']}" for r in rows) or "none"
    return (
        f"Principal {ctx.principal_id}, trust level '{ctx.envelope.trust}' in this "
        f"conversation. Linked surfaces: {linked}. Current surface: "
        f"{ctx.envelope.surface}."
    )
