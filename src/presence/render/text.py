"""Plain-text renderer. Used by the CLI, by logs, and as everyone's fallback."""

from __future__ import annotations

from presence.core.capabilities import CLI
from presence.core.reply import Reply
from presence.render.base import flatten


def to_plain(reply: Reply) -> str:
    return flatten(reply, CLI)
