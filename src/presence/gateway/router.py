"""Ingress. Normalised Envelope in, queued work out.

Everything that happens to a message before the agent sees it happens here:
dedupe, identity resolution, trust, the /link flow, and session bookkeeping.
"""

from __future__ import annotations

import asyncio
import logging

from presence.core.envelope import Envelope
from presence.core.reply import Reply
from presence.gateway import hub
from presence.store import db

log = logging.getLogger("presence.router")

QUEUE: asyncio.Queue[Envelope] = asyncio.Queue()


async def ingest(env: Envelope) -> None:
    """Called by every adapter. Must return fast -- webhooks ACK in under 3s."""
    if db.already_seen(env.surface, env.external_id):
        return

    principal_id, linked = db.principal_for(
        env.surface, env.identity.external_id, env.identity.display_name
    )
    trust = _trust(principal_id, linked, env)
    env = _with(env, principal_id=principal_id, trust=trust)

    db.touch_conversation(env.conversation.key, env.surface,
                          env.conversation.channel_id, env.conversation.thread_id,
                          principal_id)

    # Park any file now, while we still hold the bytes. The turn that acts on it
    # is usually a later one -- a lead is written after the confirmation button,
    # and that envelope is a button press with no attachment on it.
    for a in env.attachments:
        if a.data:
            db.save_upload(env.conversation.key, a.kind, a.mime, a.data)

    handled = await _commands(env, principal_id)
    if handled:
        return

    await QUEUE.put(env)


def _trust(principal_id: str, linked: bool, env: Envelope) -> str:
    if principal_id == "owner":
        return "owner" if env.conversation.is_direct else "member"
    if linked:
        return "member" if env.conversation.is_direct else "guest"
    return "guest"


def _with(env: Envelope, **changes) -> Envelope:
    from dataclasses import replace

    return replace(env, **changes)


async def _commands(env: Envelope, principal_id: str) -> bool:
    """Surface-agnostic slash commands. True when the message was consumed."""
    text = env.text.strip()

    if text.lower() in ("/start", "/help"):
        await hub.deliver(env.conversation, Reply.text(
            "I'm Presence. I'm the same agent you can reach from a terminal, this "
            "chat, and inside the app — with one memory across all of them.\n\n"
            "Try: ask me to look something up and remember it, then ask about it "
            "somewhere else.\n\n"
            "/link — connect this chat to your other surfaces\n"
            "/whoami — what I know about you here"
        ))
        return True

    if text.lower().startswith("/link"):
        parts = text.split()
        if len(parts) == 1:
            code = db.mint_link_code(principal_id)
            await hub.deliver(env.conversation, Reply.text(
                f"Your link code is {code} — it lasts 10 minutes.\n"
                f"Send `/link {code}` from another surface and I'll treat both as you."
            ))
        else:
            target = db.redeem_link_code(parts[1], env.surface, env.identity.external_id)
            await hub.deliver(env.conversation, Reply.text(
                "Linked. This is the same me you talk to elsewhere — same memory."
                if target else "That code is wrong or expired. Send /link again to get a new one."
            ))
        return True

    return False
