"""WhatsApp adapter -- a personal account, linked as a device.

Meta's Cloud API only talks to WhatsApp *Business* numbers, so it cannot reach a
personal account at all. The one protocol that can is the multi-device protocol
web.whatsapp.com itself speaks: this adapter links as another device on your
account -- the same QR scan, the same Linked Devices list -- and from then on
your own number sends and receives. neonize (Python bindings over whatsmeow) is
that client, so there is no browser and no phone tethering.

Three things to know before running it:

  * Automating a personal account is against the WhatsApp ToS and numbers do get
    banned for it. This is not an API Meta publishes. Use a spare number.
  * Your personal WhatsApp reaches everyone who has your number, so answering
    all of it automatically would be a mistake. WHATSAPP_ALLOWED gates who the
    agent replies to, and group chats are ignored unless WHATSAPP_GROUPS is set.
  * The session is a real linked device stored in WHATSAPP_SESSION. That file is
    a credential -- it can send as you until you unlink it from your phone.

This file and render/whatsapp.py are the only places that know WhatsApp exists.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any

from presence.config import settings
from presence.core.capabilities import WHATSAPP
from presence.core.envelope import (
    Attachment,
    Conversation,
    Envelope,
    Identity,
    SurfaceContext,
)
from presence.core.protocols import Sink
from presence.core.reply import Reply
from presence.render.whatsapp import media, render

log = logging.getLogger("presence.whatsapp")

USER_SERVER = "s.whatsapp.net"
GROUP_SERVER = "g.us"

# Base64 inflates by a third and the whole thing rides in the context window.
MAX_IMAGE_BYTES = 4 * 1024 * 1024

# A document and a voice note are read down to text before the model sees them,
# so what this ceiling protects is the minute spent parsing or uploading, not
# the context window. presence.media caps the text that comes out.
MAX_FILE_BYTES = 16 * 1024 * 1024

# WhatsApp reorders messages posted back-to-back often enough to scramble a
# split reply, and "…(2/3)" arriving first reads as a bug.
SEND_GAP_S = 0.25

# Reading what they sent takes a beat before any typing starts.
READ_PAUSE_S = 0.8

# Wrappers that carry the real message one level down. Disappearing messages and
# view-once photos arrive like this, and unwrapped they look like empty events.
WRAPPERS = (
    "ephemeralMessage",
    "viewOnceMessage",
    "viewOnceMessageV2",
    "viewOnceMessageV2Extension",
    "documentWithCaptionMessage",
    "editedMessage",
)


def _typing_seconds(text: str) -> float:
    """How long a person would spend producing this message. 0 when disabled."""
    if not settings.whatsapp_human_delay:
        return 0.0
    cps = max(settings.whatsapp_typing_cps, 1.0)
    return max(min(READ_PAUSE_S + len(text) / cps, settings.whatsapp_max_delay_s), 0.0)


def _has(msg: Any, field: str) -> bool:
    """Is this field actually set?

    `if msg.imageMessage` cannot answer that: a protobuf submessage is truthy
    even when absent, so every message would look like an image. HasField can,
    but it raises on a field with no presence tracking -- and _quoted sweeps
    every field of whatever message it is handed.
    """
    fd = msg.DESCRIPTOR.fields_by_name.get(field)
    return fd is not None and fd.has_presence and msg.HasField(field)


def _unwrap(msg: Any) -> Any:
    for _ in range(len(WRAPPERS)):
        for name in WRAPPERS:
            if _has(msg, name):
                msg = getattr(msg, name).message
                break
        else:
            return msg
    return msg


class WhatsAppAdapter:
    surface = "whatsapp"
    capabilities = WHATSAPP

    def __init__(self, session: str | None = None) -> None:
        self.session = session or settings.whatsapp_session
        self.client: Any = None  # neonize NewAClient, imported lazily in start()
        self._sink: Sink | None = None
        self._running = False
        self._pairing = False
        # Everything we send echoes straight back as an IsFromMe event. Holding
        # the ids is the only thing between the self-chat and an infinite loop.
        self._sent: deque[str] = deque(maxlen=256)
        # When each chat last said something, so the pacing below can count the
        # time the model already spent rather than adding to it.
        self._heard: dict[str, float] = {}

    # --- inbound ---------------------------------------------------------

    async def start(self, sink: Sink) -> None:
        try:
            from neonize.aioze.client import NewAClient
            from neonize.aioze.events import (
                ConnectedEv,
                LoggedOutEv,
                MessageEv,
                PairStatusEv,
            )
        except ImportError as e:  # neonize, or the libmagic it loads at import
            log.warning(
                "whatsapp adapter not starting -- %s. Install it with:\n"
                "  uv sync --extra whatsapp\n"
                "  brew install libmagic   (macOS; apt install libmagic1 on Debian)", e
            )
            return

        self._sink = sink
        self.client = NewAClient(self.session)

        @self.client.qr
        async def _qr_handler(_c: Any, data: bytes) -> None:
            await self._on_qr(data)

        @self.client.event(ConnectedEv)
        async def _on_connected(_c: Any, _e: Any) -> None:
            log.info("whatsapp up -- linked device online")

        @self.client.event(PairStatusEv)
        async def _on_paired(_c: Any, e: Any) -> None:
            log.info("whatsapp paired as +%s", getattr(e.ID, "User", "?"))

        @self.client.event(LoggedOutEv)
        async def _on_logged_out(_c: Any, _e: Any) -> None:
            log.warning(
                "whatsapp logged out -- the device was unlinked from the phone. "
                "Delete %s and re-scan to link again.", self.session
            )

        @self.client.event(MessageEv)
        async def _on_message(_c: Any, e: Any) -> None:
            await self._handle(e)

        self._running = True
        try:
            await self.client.connect()
            await self.client.idle()
        except asyncio.CancelledError:
            raise
        except Exception:
            # Under `serve` this task sits in the same gather() as Telegram and
            # the CLI. Letting a WhatsApp failure out of here would take both of
            # them down with it, which is a bad trade for the least reliable
            # surface in the set.
            log.exception("whatsapp connection failed -- that surface is down")

    async def _on_qr(self, data: bytes) -> None:
        """Only fires while unpaired. A configured phone number pairs by code
        instead -- typing 8 digits beats pointing a camera at a laptop."""
        if settings.whatsapp_pair_phone:
            await self._pair_by_phone()
            return
        import sys

        import segno

        print("\n  Link this agent to your WhatsApp:")
        print("  WhatsApp → Settings → Linked devices → Link a device,")
        print("  then scan this:\n")
        segno.make_qr(data).terminal(compact=True)
        # segno writes straight to the stream without flushing. Unflushed, the
        # QR is lost whenever stdout is a pipe rather than a terminal -- which
        # is every `presence whatsapp | tee`, and every supervised run.
        sys.stdout.flush()

    async def _pair_by_phone(self) -> None:
        if self._pairing:
            return  # the QR refreshes every ~20s; the code must not
        self._pairing = True
        phone = settings.whatsapp_pair_phone.lstrip("+")
        try:
            code = await self.client.PairPhone(phone, True)
        except Exception as e:
            self._pairing = False
            log.error("pair by phone failed for +%s: %s", phone, e)
            return
        print(f"\n  Pairing code for +{phone}: {code}")
        print("  WhatsApp → Settings → Linked devices → Link a device →")
        print("  Link with phone number instead, then enter that code.\n", flush=True)

    async def _handle(self, event: Any) -> None:
        """neonize dispatches this onto our loop, already as its own task."""
        try:
            env = await self._to_envelope(event)
        except Exception:
            log.exception("could not read an inbound whatsapp message")
            return
        if env is None or self._sink is None:
            return
        self._heard[env.conversation.key] = time.monotonic()
        await self._mark_read(event)
        await self.typing(env.conversation, True)
        await self._sink(env)

    async def _to_envelope(self, event: Any) -> Envelope | None:
        from neonize.utils.jid import JIDToNonAD

        info = event.Info
        src = info.MessageSource
        chat, sender = JIDToNonAD(src.Chat), JIDToNonAD(src.Sender)

        if info.ID in self._sent:
            return None  # our own reply, echoed back to us as a linked device
        if src.IsFromMe and not (settings.whatsapp_self_chat and chat.User == sender.User):
            return None  # us typing on the phone, in someone else's chat
        if src.IsGroup and not settings.whatsapp_groups:
            return None
        if not self._allowed(sender.User):
            log.debug("ignoring %s -- not in WHATSAPP_ALLOWED", sender.User)
            return None

        msg = _unwrap(event.Message)
        text, attachments, extra = await self._content(msg)
        if not text and not attachments:
            return None  # a reaction, a receipt, a poll vote -- nothing to answer

        context: dict[str, Any] = {
            "surface": "WhatsApp",
            "chat_type": "group" if src.IsGroup else "direct",
            "phone": f"+{sender.User}" if sender.Server == USER_SERVER else None,
            **extra,
        }
        if src.IsFromMe:
            # The "Message yourself" chat. Worth saying out loud: otherwise the
            # agent reads its own notes-to-self as a stranger asking questions.
            context["note"] = "this is the owner's own notes-to-self chat"
        if src.IsGroup:
            context["group_name"] = await self._group_name(src.Chat)

        return Envelope(
            identity=Identity(
                "whatsapp", sender.User, info.Pushname or None, f"+{sender.User}"
            ),
            conversation=Conversation(
                "whatsapp", self._jid_str(chat), None, not src.IsGroup
            ),
            text=text,
            capabilities=WHATSAPP,
            attachments=attachments,
            context=SurfaceContext({k: v for k, v in context.items() if v}),
            external_id=f"{chat.User}:{info.ID}",
            raw={"id": info.ID, "type": info.Type, "media_type": info.MediaType},
        )

    async def _content(self, msg: Any) -> tuple[str, list[Attachment], dict[str, Any]]:
        """The message body, whatever shape WhatsApp wrapped it in."""
        attachments: list[Attachment] = []
        extra: dict[str, Any] = {}
        text = msg.conversation

        if _has(msg, "extendedTextMessage"):
            text = msg.extendedTextMessage.text
        elif _has(msg, "imageMessage"):
            img = msg.imageMessage
            text = img.caption
            attachments.append(Attachment(
                kind="image", name="photo", mime=img.mimetype or "image/jpeg",
                data=await self._download(msg, getattr(img, "fileLength", 0)),
            ))
        elif _has(msg, "videoMessage"):
            text = msg.videoMessage.caption
            attachments.append(Attachment(
                kind="file", name="video", mime=msg.videoMessage.mimetype,
                problem="video is not something that can be watched here",
            ))
        elif _has(msg, "audioMessage"):
            audio = msg.audioMessage
            # Downloaded, not just noted: a voice note is usually the whole
            # message, and presence.media turns these bytes into what they said.
            data = await self._download(msg, getattr(audio, "fileLength", 0),
                                        MAX_FILE_BYTES)
            attachments.append(Attachment(
                kind="audio",
                name="voice note" if audio.PTT else "audio",
                mime=audio.mimetype or "audio/ogg",
                data=data,
                problem=None if data else "the recording would not download",
            ))
            extra["audio_seconds"] = audio.seconds or None
        elif _has(msg, "documentMessage"):
            doc = msg.documentMessage
            text = doc.caption
            data = await self._download(msg, getattr(doc, "fileLength", 0),
                                        MAX_FILE_BYTES)
            attachments.append(Attachment(
                kind="file", name=doc.fileName or doc.title, mime=doc.mimetype,
                data=data,
                problem=None if data else "the file would not download",
            ))
        elif _has(msg, "stickerMessage"):
            text = "[sticker]"

        # Button taps, on the off-chance a phone rendered one. The numbered-list
        # fallback is the path that actually runs -- see render/whatsapp.py.
        for field, attr in (("buttonsResponseMessage", "selectedButtonID"),
                            ("templateButtonReplyMessage", "selectedID")):
            if _has(msg, field):
                choice = getattr(getattr(msg, field), attr)
                text = text or getattr(getattr(msg, field), "selectedDisplayText", "")
                extra["choice_id"] = choice
        if _has(msg, "listResponseMessage"):
            reply = msg.listResponseMessage.singleSelectReply
            extra["choice_id"] = reply.selectedRowID
            text = text or msg.listResponseMessage.title

        quoted = self._quoted(msg)
        if quoted:
            extra["replying_to"] = quoted[:300]

        return (text or "").strip(), attachments, extra

    def _quoted(self, msg: Any) -> str:
        """The text of the message this one is a reply to, if any."""
        for field in msg.DESCRIPTOR.fields_by_name:
            if not _has(msg, field):
                continue
            sub = getattr(msg, field)
            if not hasattr(sub, "DESCRIPTOR") or not _has(sub, "contextInfo"):
                continue
            ctx = sub.contextInfo
            if not _has(ctx, "quotedMessage"):
                continue
            q = _unwrap(ctx.quotedMessage)
            return (q.conversation
                    or (q.extendedTextMessage.text if _has(q, "extendedTextMessage") else "")
                    or (q.imageMessage.caption if _has(q, "imageMessage") else ""))
        return ""

    async def _download(self, msg: Any, size_hint: int,
                        limit: int = MAX_IMAGE_BYTES) -> bytes | None:
        """Media bytes, or None. Never raises: a photo that will not download is
        still a conversation, and the agent can ask them to type it instead."""
        if size_hint and size_hint > limit:
            log.warning("attachment too large (%d bytes), skipping", size_hint)
            return None
        try:
            data = await self.client.download_any(msg)
        except Exception as e:
            log.warning("media download failed: %s", e)
            return None
        if data and len(data) > limit:
            log.warning("attachment too large (%d bytes), skipping", len(data))
            return None
        return data

    async def _group_name(self, chat: Any) -> str | None:
        try:
            return (await self.client.get_group_info(chat)).GroupName.Name or None
        except Exception:
            return None

    async def _mark_read(self, event: Any) -> None:
        if not settings.whatsapp_mark_read:
            return
        from neonize.utils.enum import ReceiptType

        try:
            await self.client.mark_read(
                event.Info.ID,
                chat=event.Info.MessageSource.Chat,
                sender=event.Info.MessageSource.Sender,
                receipt=ReceiptType.READ,
            )
        except Exception as e:
            log.debug("mark read failed: %s", e)

    @staticmethod
    def _allowed(user: str) -> bool:
        if not settings.whatsapp_allowed:
            return True
        return user.lstrip("+") in {n.lstrip("+") for n in settings.whatsapp_allowed}

    # --- addressing ------------------------------------------------------

    @staticmethod
    def _jid_str(jid: Any) -> str:
        """JID -> "8801700000000@s.whatsapp.net".

        Conversation.key joins on ":" and from_key splits on it, so the device
        suffix a raw JID can carry (user:3@server) would corrupt the key. Chat
        JIDs are always device 0, and callers pass them through JIDToNonAD.
        """
        return f"{jid.User}@{jid.Server}" if jid.User else jid.Server

    @staticmethod
    def _parse_jid(value: str) -> Any:
        from neonize.utils.jid import build_jid

        user, _, server = value.partition("@")
        return build_jid(user, server or USER_SERVER)

    # --- outbound --------------------------------------------------------

    async def send(self, conv: Conversation, reply: Reply) -> str:
        if self.client is None:
            return ""
        jid = self._parse_jid(conv.channel_id)
        chunks = render(reply)
        images = media(reply)
        last = ""

        for i, chunk in enumerate(chunks):
            await self._compose(conv, chunk, first=i == 0)
            last = await self._send_one(jid, chunk) or last

        for image in images:
            await asyncio.sleep(SEND_GAP_S)
            try:
                resp = await self.client.send_image(jid, image.data, caption=image.name)
                last = self._remember(resp) or last
            except Exception as e:
                log.warning("image send failed: %s", e)
                last = await self._send_one(jid, f"[image: {image.name}]") or last

        if not chunks and not images:
            last = await self._send_one(jid, reply.plain()[:WHATSAPP.max_chars]) or last

        await self.typing(conv, False)
        return last

    async def _compose(self, conv: Conversation, text: str, first: bool) -> None:
        """Hold the typing indicator for as long as this message would take.

        A four-line answer that lands 200ms after they hit send was not typed by
        anybody, and that reads as machinery before they have taken in a word of
        it. So the indicator stays up for roughly as long as the message would
        take to write, capped so nobody is left waiting on theatre. WhatsApp
        expires a COMPOSING presence after about ten seconds, which is why it is
        re-sent per chunk rather than once at the top.
        """
        pause = _typing_seconds(text)
        if first:
            # Thinking and tool calls have already kept them waiting, with the
            # indicator up the whole time. Pacing is meant to cover a reply that
            # arrives too fast, not to tax one that was already slow.
            waited = time.monotonic() - self._heard.pop(conv.key, time.monotonic())
            pause -= max(waited, 0.0)
        if pause <= 0:
            if not first:
                await asyncio.sleep(SEND_GAP_S)
            return
        await self.typing(conv, True)
        await asyncio.sleep(pause)

    async def _send_one(self, jid: Any, text: str) -> str:
        try:
            return self._remember(await self.client.send_message(jid, text))
        except Exception as e:
            log.warning("send failed: %s", e)
            return ""

    def _remember(self, resp: Any) -> str:
        """Record the id so the echo of our own message is not read as inbound."""
        message_id = getattr(resp, "ID", "") or ""
        if message_id:
            self._sent.append(message_id)
        return message_id

    async def edit(self, conv: Conversation, message_id: str, reply: Reply) -> None:
        """WhatsApp allows edits for about 15 minutes. WHATSAPP.supports_streaming
        is False, so the worker never streams into one -- this is here for a
        caller that wants to correct a message it already sent."""
        if self.client is None or not message_id:
            return
        from neonize.proto.waE2E.WAWebProtobufsE2E_pb2 import Message as WAMessage

        chunks = render(reply)
        if not chunks:
            return
        try:
            await self.client.edit_message(
                self._parse_jid(conv.channel_id), message_id,
                WAMessage(conversation=chunks[0]),
            )
        except Exception as e:
            log.debug("edit failed: %s", e)

    async def typing(self, conv: Conversation, on: bool = True) -> None:
        if self.client is None:
            return
        from neonize.utils.enum import ChatPresence, ChatPresenceMedia

        state = (ChatPresence.CHAT_PRESENCE_COMPOSING if on
                 else ChatPresence.CHAT_PRESENCE_PAUSED)
        try:
            await self.client.send_chat_presence(
                self._parse_jid(conv.channel_id), state,
                ChatPresenceMedia.CHAT_PRESENCE_MEDIA_TEXT,
            )
        except Exception as e:
            # Fails outright when the account's privacy setting hides "online".
            log.debug("chat presence failed: %s", e)

    async def stop(self) -> None:
        """Close the connection as far as the library allows.

        Not far enough, as it turns out. The connection is a blocking Go call
        parked on a non-daemon worker thread, and in neonize 0.4.3 none of
        disconnect(), stop() or cancelling the connect task releases that thread
        while the client is unpaired -- measured, all three leave it running.
        Python then hangs at interpreter exit joining it, so __main__ leaves the
        process outright once everything here has been shut down.
        """
        self._running = False
        client, self.client = self.client, None
        if client is None:
            return
        for step in (client.disconnect, client.stop):
            try:
                await step()
            except Exception as e:
                log.debug("whatsapp %s failed: %s", step.__name__, e)
        task = getattr(client, "connect_task", None)
        if task is not None and not task.done():
            task.cancel()
