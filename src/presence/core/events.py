"""What the agent runtime streams out while it works.

The worker consumes this stream and decides, using the surface's capabilities,
what to actually show. ThinkingDelta is never rendered to a user.

FROZEN CONTRACT. Additive changes only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from presence.core.reply import Choice, Reply


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ThinkingDelta:
    text: str


@dataclass(frozen=True)
class ToolStarted:
    call_id: str
    name: str
    args: dict[str, Any] = field(default_factory=dict)

    def human(self) -> str:
        """A short progress line, for surfaces that cannot stream tokens."""
        return _PROGRESS.get(self.name, f"Working ({self.name})…")


@dataclass(frozen=True)
class ToolFinished:
    call_id: str
    name: str
    ok: bool
    preview: str
    ms: int


@dataclass(frozen=True)
class NeedsConfirm:
    call_id: str
    name: str
    summary: str
    choices: list[Choice]


@dataclass(frozen=True)
class Final:
    reply: Reply


@dataclass(frozen=True)
class Failed:
    message: str


AgentEvent = (
    TextDelta | ThinkingDelta | ToolStarted | ToolFinished | NeedsConfirm | Final | Failed
)

_PROGRESS = {
    "web_search": "Searching the web…",
    "web_fetch": "Reading a page…",
    "recall": "Checking what I remember…",
    "remember": "Making a note…",
    "schedule": "Setting a reminder…",
    "send_to": "Reaching you on another surface…",
}
