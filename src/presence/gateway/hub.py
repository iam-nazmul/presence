"""The live adapter registry.

Anything that needs to deliver a message -- the worker, the scheduler, the
crosspost tool -- goes through here, so nothing outside adapters/ needs to know
which surfaces happen to be running.
"""

from __future__ import annotations

from presence.core.envelope import Conversation
from presence.core.protocols import Adapter
from presence.core.reply import Reply

ADAPTERS: dict[str, Adapter] = {}


def register(adapter: Adapter) -> None:
    ADAPTERS[adapter.surface] = adapter


def get(surface: str) -> Adapter | None:
    return ADAPTERS.get(surface)


def live_surfaces() -> list[str]:
    return sorted(ADAPTERS)


async def deliver(conv: Conversation, reply: Reply) -> str | None:
    """Send a reply to a conversation on whichever adapter owns that surface."""
    adapter = ADAPTERS.get(conv.surface)
    if adapter is None:
        return None
    return await adapter.send(conv, reply)


async def deliver_to_key(conv_key: str, reply: Reply) -> bool:
    conv = Conversation.from_key(conv_key)
    return await deliver(conv, reply) is not None
