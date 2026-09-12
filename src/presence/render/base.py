"""Shared downcast logic.

Rules that are the same everywhere:
  * text longer than max_chars splits on paragraph, then sentence, then hard cut
  * more choices than max_buttons degrades to a numbered list
  * a block a surface cannot do degrades to its text form -- never dropped
"""

from __future__ import annotations

import re

from presence.core.capabilities import SurfaceCapabilities
from presence.core.reply import (
    CardBlock,
    ChoiceBlock,
    CodeBlock,
    FileBlock,
    Reply,
    TextBlock,
)


def split_text(text: str, max_chars: int) -> list[str]:
    """Split into deliverable chunks, preferring paragraph then sentence breaks."""
    text = text.strip()
    if len(text) <= max_chars:
        return [text] if text else []

    chunks: list[str] = []
    remaining = text
    while len(remaining) > max_chars:
        window = remaining[:max_chars]
        cut = window.rfind("\n\n")
        if cut < max_chars * 0.4:
            cut = max(window.rfind(". "), window.rfind("\n"))
        if cut < max_chars * 0.3:
            cut = max_chars
        chunks.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        chunks.append(remaining)

    total = len(chunks)
    return [f"{c}\n\n…({i}/{total})" if total > 1 and i < total else c
            for i, c in enumerate(chunks, 1)]


def choices_as_list(block: ChoiceBlock) -> str:
    """The no-buttons fallback. The router accepts a bare '2' as the answer."""
    lines = [f"{i}. {c.label}" for i, c in enumerate(block.choices, 1)]
    return f"{block.prompt}\n" + "\n".join(lines) + "\n\nReply with a number."


def card_as_text(block: CardBlock) -> str:
    lines = [f"**{block.title}**"] + [f"{k}: {v}" for k, v in block.fields.items()]
    if block.url:
        lines.append(block.url)
    return "\n".join(lines)


def flatten(reply: Reply, caps: SurfaceCapabilities) -> str:
    """Every block as one markdown-ish string. The universal fallback."""
    out: list[str] = []
    for b in reply.blocks:
        if isinstance(b, TextBlock):
            out.append(f"> {b.text}" if b.style == "quote" else b.text)
        elif isinstance(b, CodeBlock):
            out.append(f"```{b.lang or ''}\n{b.code}\n```")
        elif isinstance(b, ChoiceBlock):
            out.append(choices_as_list(b))
        elif isinstance(b, CardBlock):
            out.append(card_as_text(b))
        elif isinstance(b, FileBlock):
            out.append(f"[file: {b.name}]" + (f" {b.url}" if b.url else ""))
    return "\n\n".join(x for x in out if x)


def strip_markdown(text: str) -> str:
    """For surfaces that render nothing -- WhatsApp, SMS."""
    text = re.sub(r"```[a-z]*\n?", "", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"\1", text)
    text = re.sub(r"`(.+?)`", r"\1", text)
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.M)
    text = re.sub(r"\[(.+?)\]\((.+?)\)", r"\1 (\2)", text)
    return text.strip()
