"""The WhatsApp downcast, and the two ways this surface can hurt you.

Everything here runs without neonize installed: the renderer is pure text, and
the adapter's checks are deliberately reachable without a live client.
"""

from __future__ import annotations

import asyncio

import pytest

from presence.adapters.whatsapp import WhatsAppAdapter
from presence.core.capabilities import WHATSAPP
from presence.core.reply import (
    CardBlock,
    Choice,
    ChoiceBlock,
    CodeBlock,
    FileBlock,
    Reply,
    TextBlock,
)
from presence.render.whatsapp import media, render, to_whatsapp


def one(reply: Reply) -> str:
    chunks = render(reply)
    assert len(chunks) == 1, chunks
    return chunks[0]


# --- markup ---------------------------------------------------------------


def test_markdown_bold_becomes_whatsapp_bold() -> None:
    assert to_whatsapp("**shipped** today") == "*shipped* today"


def test_markdown_italic_does_not_collide_with_bold() -> None:
    """*x* is italic in markdown and bold on WhatsApp. Rewriting naively in one
    pass turns every bold into an italic, which is the whole reason for the
    sentinel in to_whatsapp()."""
    assert to_whatsapp("**bold** and *italic*") == "*bold* and _italic_"


def test_headings_and_bullets_degrade() -> None:
    out = to_whatsapp("## Status\n- one\n- two")
    assert out == "*Status*\n• one\n• two"


def test_links_keep_the_url() -> None:
    assert to_whatsapp("[the docs](https://x.dev/a)") == "the docs (https://x.dev/a)"
    assert to_whatsapp("[](https://x.dev/a)") == "https://x.dev/a"


def test_code_fences_survive_untouched() -> None:
    """* and ` inside a code block are code, not markup."""
    out = to_whatsapp("```python\nx = a**b * c\n```")
    assert out == "```\nx = a**b * c\n```"


def test_choices_degrade_to_a_numbered_list() -> None:
    """WHATSAPP.supports_buttons is True, but a personal account draws nothing
    for an interactive payload -- so the confirm flow rides on the numbered
    list that worker._confirmation_answer accepts a bare "1" against."""
    reply = Reply(blocks=[ChoiceBlock(
        prompt="Save this lead?",
        choices=[Choice("confirm:r:c:yes", "Yes"), Choice("confirm:r:c:no", "No")],
    )])
    out = one(reply)
    assert "1. Yes" in out and "2. No" in out
    assert "Reply with a number." in out


def test_a_card_keeps_its_fields() -> None:
    out = one(Reply(blocks=[CardBlock("Lead", {"Name": "Rina", "Phone": "+8801700"})]))
    assert out.startswith("*Lead*")
    assert "Name: Rina" in out and "Phone: +8801700" in out


def test_long_replies_split_within_the_limit() -> None:
    chunks = render(Reply.text("para. " * 2000))
    assert len(chunks) > 1
    assert all(len(c) <= WHATSAPP.max_chars for c in chunks)


def test_code_block_renders_as_monospace() -> None:
    out = one(Reply(blocks=[CodeBlock("print(1)", "python")]))
    assert out == "```\nprint(1)\n```"


# --- media ----------------------------------------------------------------


def test_images_leave_the_text_body_and_ride_as_media() -> None:
    reply = Reply(blocks=[
        TextBlock("Here it is"),
        FileBlock("chart.png", "image/png", data=b"\x89PNG"),
    ])
    assert one(reply) == "Here it is"
    assert [b.name for b in media(reply)] == ["chart.png"]


def test_a_file_that_cannot_ride_as_media_stays_in_the_text() -> None:
    """WHATSAPP.supports_files is False, so a PDF must not vanish silently."""
    reply = Reply(blocks=[FileBlock("report.pdf", "application/pdf", url="https://x.dev/r")])
    assert media(reply) == []
    assert "report.pdf" in one(reply)


# --- sending --------------------------------------------------------------


class FakeClient:
    """Just enough of neonize to exercise send() without a linked phone."""

    def __init__(self) -> None:
        self.texts: list[str] = []
        self.images: list[tuple[bytes, str]] = []
        self.presence: list[str] = []
        self._n = 0

    def _resp(self):
        self._n += 1
        return type("SendResponse", (), {"ID": f"OUT{self._n}"})()

    async def send_message(self, jid, text):
        self.texts.append(text)
        return self._resp()

    async def send_image(self, jid, data, caption=None):
        self.images.append((data, caption))
        return self._resp()

    async def send_chat_presence(self, jid, state, medium):
        self.presence.append(state.name)


@pytest.fixture()
def wired(monkeypatch):
    """An adapter with a fake client and no real neonize import in the way."""
    import sys
    import types

    jid_mod = types.ModuleType("neonize.utils.jid")
    jid_mod.build_jid = lambda user, server="s.whatsapp.net": f"{user}@{server}"
    enum_mod = types.ModuleType("neonize.utils.enum")
    enum_mod.ChatPresence = type("ChatPresence", (), {
        "CHAT_PRESENCE_COMPOSING": type("S", (), {"name": "COMPOSING"})(),
        "CHAT_PRESENCE_PAUSED": type("S", (), {"name": "PAUSED"})(),
    })
    enum_mod.ChatPresenceMedia = type("M", (), {"CHAT_PRESENCE_MEDIA_TEXT": object()})
    for name, mod in (("neonize.utils.jid", jid_mod), ("neonize.utils.enum", enum_mod)):
        monkeypatch.setitem(sys.modules, name, mod)

    adapter = WhatsAppAdapter(session=":memory:")
    adapter.client = FakeClient()
    return adapter


@pytest.fixture()
def conv():
    from presence.core.envelope import Conversation

    return Conversation("whatsapp", "8801700000000@s.whatsapp.net", None, True)


def test_send_records_its_own_ids_so_the_echo_is_not_answered(wired, conv) -> None:
    """Every message we send comes straight back as an IsFromMe event. In the
    self-chat that echo is indistinguishable from the owner typing, so without
    the id the agent answers itself, forever."""
    returned = asyncio.run(wired.send(conv, Reply.text("hello")))
    assert wired.client.texts == ["hello"]
    assert returned in wired._sent


def test_a_split_reply_goes_out_in_order(wired, conv) -> None:
    asyncio.run(wired.send(conv, Reply.text("para. " * 2000)))
    sent = wired.client.texts
    total = len(sent)
    assert total > 1
    # "…(2/4)" arriving before "…(1/4)" reads as a bug, so order is the point.
    assert [t.endswith(f"…({i}/{total})") for i, t in enumerate(sent[:-1], 1)] == \
        [True] * (total - 1)
    assert all(f"OUT{i}" in wired._sent for i in range(1, total + 1))


def test_an_image_rides_as_media_and_its_stub_leaves_the_text(wired, conv) -> None:
    asyncio.run(wired.send(conv, Reply(blocks=[
        TextBlock("the chart"),
        FileBlock("chart.png", "image/png", data=b"\x89PNG"),
    ])))
    assert wired.client.texts == ["the chart"]
    assert wired.client.images == [(b"\x89PNG", "chart.png")]


def test_typing_stops_after_the_reply_lands(wired, conv) -> None:
    async def go():
        await wired.typing(conv, True)
        await wired.send(conv, Reply.text("done"))

    asyncio.run(go())
    assert wired.client.presence == ["COMPOSING", "PAUSED"]


# --- who the agent answers ------------------------------------------------


@pytest.fixture()
def allowlist(monkeypatch):
    from presence.config import settings

    def _set(numbers: list[str]):
        monkeypatch.setattr(settings, "whatsapp_allowed", numbers)

    return _set


def test_an_empty_allowlist_answers_everyone(allowlist) -> None:
    allowlist([])
    assert WhatsAppAdapter._allowed("8801999999999")


def test_the_allowlist_shuts_out_everyone_else(allowlist) -> None:
    """A personal number is reachable by anyone who has it. This is the check
    that stops the agent holding conversations you never agreed to."""
    allowlist(["8801700000000"])
    assert WhatsAppAdapter._allowed("8801700000000")
    assert WhatsAppAdapter._allowed("+8801700000000"), "a leading + is the same number"
    assert not WhatsAppAdapter._allowed("8801999999999")


# --- addressing -----------------------------------------------------------


def test_a_jid_survives_the_conversation_key_round_trip() -> None:
    """Conversation.key joins on ':' and from_key splits on it, so a JID that
    smuggled in a device suffix would come back corrupted."""
    from presence.core.envelope import Conversation

    conv = Conversation("whatsapp", "8801700000000@s.whatsapp.net", None, True)
    assert Conversation.from_key(conv.key).channel_id == conv.channel_id


def test_group_and_user_jids_both_round_trip() -> None:
    class FakeJID:
        def __init__(self, user, server):
            self.User, self.Server = user, server

    for user, server in (("8801700000000", "s.whatsapp.net"), ("120363000", "g.us")):
        assert WhatsAppAdapter._jid_str(FakeJID(user, server)) == f"{user}@{server}"
