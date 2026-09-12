"""The neutral outbound message.

The agent never writes Slack JSON or Telegram markup. It emits blocks; the
per-surface renderers in presence/render/ downcast them to whatever that
surface can actually do.

FROZEN CONTRACT. Additive changes only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class TextBlock:
    text: str
    style: Literal["body", "heading", "quote", "error"] = "body"


@dataclass(frozen=True)
class CodeBlock:
    code: str
    lang: str | None = None


@dataclass(frozen=True)
class Choice:
    id: str
    label: str
    style: Literal["default", "primary", "danger"] = "default"


@dataclass(frozen=True)
class ChoiceBlock:
    """Buttons where possible, a numbered list where not.

    Used for confirmations, disambiguation and next-step suggestions. When a
    surface cannot render buttons the renderer emits a numbered list and the
    router accepts a bare "2" as the answer.
    """

    prompt: str
    choices: list[Choice]


@dataclass(frozen=True)
class CardBlock:
    title: str
    fields: dict[str, str]
    url: str | None = None


@dataclass(frozen=True)
class FileBlock:
    name: str
    mime: str
    data: bytes | None = None
    url: str | None = None


Block = TextBlock | CodeBlock | ChoiceBlock | CardBlock | FileBlock


@dataclass
class Reply:
    blocks: list[Block] = field(default_factory=list)
    ephemeral: bool = False
    reply_in_thread: bool = True

    @staticmethod
    def text(s: str) -> Reply:
        return Reply(blocks=[TextBlock(s)])

    @staticmethod
    def error(s: str) -> Reply:
        return Reply(blocks=[TextBlock(s, style="error")])

    def plain(self) -> str:
        """Lowest-common-denominator string. Used for logs and previews."""
        from presence.render.text import to_plain

        return to_plain(self)
