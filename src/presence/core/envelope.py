"""The universal inbound message.

Every surface normalises into an Envelope. Nothing downstream of the gateway
knows or cares which surface a message came from -- it reads the fields here.

FROZEN CONTRACT. Additive changes only.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from presence.core.capabilities import SurfaceCapabilities

Surface = Literal["cli", "telegram", "whatsapp", "slack", "webapp", "erpnext", "system"]

TrustLevel = Literal["owner", "member", "guest"]
# owner  : linked Principal, in a direct/private conversation
# member : known identity, but in a shared space (channel, group chat)
# guest  : unrecognised sender. Read-only tools, no memory writes.


@dataclass(frozen=True)
class Identity:
    """Who sent it, in the surface's own vocabulary."""

    surface: Surface
    external_id: str
    display_name: str | None = None
    handle: str | None = None

    @property
    def key(self) -> str:
        return f"{self.surface}:{self.external_id}"


@dataclass(frozen=True)
class Conversation:
    """Where the reply must go back to. This tuple is the addressing key."""

    surface: Surface
    channel_id: str
    thread_id: str | None = None
    is_direct: bool = True

    @property
    def key(self) -> str:
        return f"{self.surface}:{self.channel_id}:{self.thread_id or '-'}"

    @staticmethod
    def from_key(key: str) -> Conversation:
        surface, channel_id, thread = key.split(":", 2)
        return Conversation(
            surface=surface,  # type: ignore[arg-type]
            channel_id=channel_id,
            thread_id=None if thread == "-" else thread,
        )


@dataclass(frozen=True)
class Attachment:
    kind: Literal["image", "audio", "file", "link"]
    name: str | None = None
    mime: str | None = None
    url: str | None = None
    data: bytes | None = None


@dataclass(frozen=True)
class SurfaceContext:
    """Everything the surface knows that the user did not have to type.

    THIS FIELD IS THE PROJECT. An adapter that leaves this empty has failed its
    only interesting job.

      webapp   : {"url":..., "page_title":..., "selection":...}
      slack    : {"channel_name":"#ops", "topic":...}
      telegram : {"locale":"bn", "tz":"Asia/Dhaka", "reply_to_text":...}
      cli      : {"cwd":..., "git_branch":..., "os":...}
    """

    data: dict[str, Any] = field(default_factory=dict)

    def render(self) -> str:
        """Human/model readable block. Empty string when there is nothing."""
        rows = [f"- {k}: {v}" for k, v in self.data.items() if v not in (None, "", [])]
        return "\n".join(rows)


@dataclass(frozen=True)
class Envelope:
    identity: Identity
    conversation: Conversation
    text: str
    capabilities: SurfaceCapabilities
    trust: TrustLevel = "guest"
    principal_id: str | None = None  # filled by the gateway, not the adapter
    attachments: list[Attachment] = field(default_factory=list)
    context: SurfaceContext = field(default_factory=SurfaceContext)
    external_id: str | None = None  # the surface's own message id, for dedupe
    raw: dict[str, Any] = field(default_factory=dict)  # NEVER shown to the model
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    ts: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def surface(self) -> Surface:
        return self.conversation.surface
