"""Proactivity.

A due trigger becomes a synthetic Envelope on the same queue as a real message,
addressed to whichever conversation the person was last active in. There is no
separate proactive code path -- that is the point of the harness.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from presence.core.capabilities import BY_SURFACE
from presence.core.envelope import Conversation, Envelope, Identity, SurfaceContext
from presence.gateway import hub
from presence.gateway.router import QUEUE
from presence.store import db

log = logging.getLogger("presence.scheduler")

PERIOD_S = 20


async def tick_once() -> int:
    fired = 0
    for t in db.due_triggers():
        conv_key = t["target_conv_key"] or db.preferred_conversation(t["principal_id"])
        if not conv_key:
            log.warning("trigger %s has nowhere to go", t["id"])
            db.fire_trigger(t["id"])
            continue

        conv = Conversation.from_key(conv_key)
        if conv.surface not in hub.live_surfaces():
            continue  # wait for that surface to come back up

        env = Envelope(
            identity=Identity(conv.surface, "scheduler", "scheduler"),
            conversation=conv,
            text=t["prompt"],
            capabilities=BY_SURFACE.get(conv.surface, BY_SURFACE["telegram"]),
            trust="owner",
            principal_id=t["principal_id"],
            context=SurfaceContext({
                "scheduled": True,
                "why": "a reminder this person asked you to set earlier",
                "set_at": t["created_at"],
            }),
            external_id=f"trigger:{t['id']}:{uuid.uuid4().hex[:8]}",
        )
        await QUEUE.put(env)
        db.fire_trigger(t["id"])
        fired += 1
        log.info("fired trigger %s onto %s", t["id"], conv_key)
    return fired


async def run_scheduler() -> None:
    log.info("scheduler ticking every %ss", PERIOD_S)
    while True:
        try:
            await tick_once()
        except Exception:
            log.exception("scheduler tick failed")
        await asyncio.sleep(PERIOD_S)
