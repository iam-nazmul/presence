"""The four swap points.

Each of these is a seam. Implementations live behind them and can be replaced
without touching anything else -- that is the whole claim of the harness.

FROZEN CONTRACT. Additive changes only.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Protocol, runtime_checkable

from presence.core.capabilities import SurfaceCapabilities
from presence.core.envelope import Conversation, Envelope, Surface
from presence.core.events import AgentEvent
from presence.core.reply import Reply

Sink = Callable[[Envelope], Awaitable[None]]


@runtime_checkable
class Adapter(Protocol):
    """A surface. The ONLY place a surface SDK may be imported."""

    surface: Surface
    capabilities: SurfaceCapabilities

    async def start(self, sink: Sink) -> None:
        """Begin receiving. Call sink(envelope) per inbound message. Runs forever."""
        ...

    async def send(self, conv: Conversation, reply: Reply) -> str:
        """Deliver a reply. Returns the surface's message id."""
        ...

    async def edit(self, conv: Conversation, message_id: str, reply: Reply) -> None:
        """Update a message in place. No-op when not supports_streaming."""
        ...

    async def typing(self, conv: Conversation, on: bool) -> None:
        ...

    async def stop(self) -> None:
        ...



@runtime_checkable
class ChatProvider(Protocol):
    name: str

    def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        model: str,
        **opts: Any,
    ) -> AsyncIterator[Any]:
        ...


@runtime_checkable
class AgentRuntime(Protocol):
    def run(self, envelope: Envelope) -> AsyncIterator[AgentEvent]:
        ...

