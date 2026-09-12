"""WhatsApp downcast.

WhatsApp is the narrowest surface in the harness. It has no headings, no
tables, no labelled links, and a markup dialect of its own: ``*bold*``,
``_italic_``, ``~strike~``, ```` ```mono``` ````. WHATSAPP.markdown is "none"
precisely so the model is told to write plain prose -- but a model that has
spent its whole life emitting markdown will still reach for ** now and then, so
this translates rather than trusting the prompt to hold.

Buttons are deliberately not rendered. whatsmeow can put an interactive payload
on the wire, but a *personal* account is not a Business account and the phones
on the other end mostly draw nothing at all -- which would silently eat every
confirmation prompt. flatten() already degrades a ChoiceBlock to a numbered
list, and worker._confirmation_answer accepts a bare "1"/"2" against it, so the
confirm flow works here without buttons existing.
"""

from __future__ import annotations

import re

from presence.core.capabilities import WHATSAPP
from presence.core.reply import FileBlock, Reply
from presence.render.base import flatten, split_text

# Bold is *x* on WhatsApp but **x** in markdown, and *x* in markdown is italic.
# Rewriting both in place would make bold eat italic, so bold parks on a
# sentinel no model output contains and is restored last.
_BOLD = "\x00"
_FENCE = "\x01"

FENCE_RE = re.compile(r"```[a-zA-Z]*\n?(.*?)```", re.S)


def _link(m: re.Match[str]) -> str:
    """[label](url) -> label (url). WhatsApp autolinks a bare URL; it cannot
    hide one behind a label, and dropping the URL would lose the citation."""
    label, url = m.group(1).strip(), m.group(2)
    return url if label in ("", url) else f"{label} ({url})"


def to_whatsapp(text: str) -> str:
    """Markdown-ish in, WhatsApp markup out."""
    # Park fenced code first: everything below rewrites * and `, and neither
    # means anything inside a code block.
    blocks: list[str] = []

    def park(m: re.Match[str]) -> str:
        blocks.append(m.group(1).strip("\n"))
        return f"{_FENCE}{len(blocks) - 1}{_FENCE}"

    out = FENCE_RE.sub(park, text)

    out = re.sub(r"`([^`\n]+?)`", r"```\1```", out)                       # inline code
    out = re.sub(r"^\s*#{1,6}\s*(.+?)\s*#*$", rf"{_BOLD}\1{_BOLD}", out, flags=re.M)
    out = re.sub(r"^(\s*)[-*+]\s+", r"\1• ", out, flags=re.M)             # bullets
    out = re.sub(r"\*\*(.+?)\*\*", rf"{_BOLD}\1{_BOLD}", out, flags=re.S)  # **bold**
    out = re.sub(r"__(.+?)__", rf"{_BOLD}\1{_BOLD}", out, flags=re.S)      # __bold__
    out = re.sub(r"~~(.+?)~~", r"~\1~", out, flags=re.S)                   # ~~strike~~
    out = re.sub(r"(?<![\w*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\w*])", r"_\1_", out)
    out = re.sub(r"\[(.*?)\]\((https?://[^\s)]+)\)", _link, out)
    out = out.replace(_BOLD, "*")

    for i, block in enumerate(blocks):
        out = out.replace(f"{_FENCE}{i}{_FENCE}", f"```\n{block}\n```")
    return out.strip()


def media(reply: Reply) -> list[FileBlock]:
    """Blocks that can ride as real WhatsApp media instead of a text stub.

    Images only: WHATSAPP.supports_files is False, so a document stays the
    "[file: name] url" line flatten() gives it.
    """
    return [
        b for b in reply.blocks
        if isinstance(b, FileBlock) and b.data and (b.mime or "").startswith("image/")
    ]


def render(reply: Reply) -> list[str]:
    """One string per outbound WhatsApp message. Empty when there is only media."""
    sendable = {id(b) for b in media(reply)}
    text_only = Reply(blocks=[b for b in reply.blocks if id(b) not in sendable])
    body = to_whatsapp(flatten(text_only, WHATSAPP))
    # Headroom for the "…(1/3)" counter split_text appends to a split message.
    return split_text(body, WHATSAPP.max_chars - 100)
