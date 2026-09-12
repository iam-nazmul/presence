"""Telegram downcast.

HTML parse mode rather than MarkdownV2: escaping is deterministic, so a stray
underscore in a URL cannot 400 the whole message mid-demo.
"""

from __future__ import annotations

import html
import re

from presence.core.capabilities import TELEGRAM
from presence.core.reply import ChoiceBlock, Reply
from presence.render.base import flatten, split_text


def to_html(text: str) -> str:
    """Escape everything, then re-introduce the few tags Telegram accepts."""
    out = html.escape(text)
    out = re.sub(r"```(?:[a-zA-Z]*)\n(.*?)```", r"<pre>\1</pre>", out, flags=re.S)
    out = re.sub(r"`([^`\n]+?)`", r"<code>\1</code>", out)
    out = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", out, flags=re.S)
    out = re.sub(r"^#{1,6}\s*(.+)$", r"<b>\1</b>", out, flags=re.M)
    out = re.sub(r"\[(.+?)\]\((https?://[^\s)]+)\)", r'<a href="\2">\1</a>', out)
    return out


def keyboard(reply: Reply) -> dict | None:
    """Inline keyboard from the first ChoiceBlock that fits."""
    for b in reply.blocks:
        if isinstance(b, ChoiceBlock) and len(b.choices) <= TELEGRAM.max_buttons:
            return {"inline_keyboard": [[{"text": c.label, "callback_data": c.id[:64]}]
                                        for c in b.choices]}
    return None


def render(reply: Reply) -> list[dict]:
    """One dict per outbound Telegram message."""
    body = flatten(reply, TELEGRAM)
    chunks = split_text(body, TELEGRAM.max_chars - 200) or [" "]
    kb = keyboard(reply)
    msgs = [{"text": to_html(c), "parse_mode": "HTML",
             "link_preview_options": {"is_disabled": True}} for c in chunks]
    if kb:
        msgs[-1]["reply_markup"] = kb
    return msgs
