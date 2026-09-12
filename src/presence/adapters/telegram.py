"""Telegram adapter. Long polling -- needs no public URL, so it cannot be
broken by a dead tunnel on demo day.

This file and render/telegram.py are the only places that know Telegram exists.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from presence.config import settings
from presence.core.capabilities import TELEGRAM
from presence.core.envelope import (
    Attachment,
    Conversation,
    Envelope,
    Identity,
    SurfaceContext,
)
from presence.core.protocols import Sink
from presence.core.reply import Reply
from presence.render.telegram import render

log = logging.getLogger("presence.telegram")

# Base64 inflates by a third and the whole thing rides in the context window.
MAX_PHOTO_BYTES = 4 * 1024 * 1024


class TelegramAdapter:
    surface = "telegram"
    capabilities = TELEGRAM

    def __init__(self, token: str | None = None) -> None:
        self.token = token or settings.telegram_token
        self.api = f"https://api.telegram.org/bot{self.token}"
        self._http = httpx.AsyncClient(timeout=45)
        self._running = False

    # --- inbound ---------------------------------------------------------

    async def start(self, sink: Sink) -> None:
        if not self.token:
            log.warning("no TELEGRAM_BOT_TOKEN, telegram adapter not starting")
            return
        me = (await self._http.get(f"{self.api}/getMe")).json()
        log.info("telegram up as @%s", me.get("result", {}).get("username"))
        self._running = True
        offset: int | None = None

        while self._running:
            try:
                r = await self._http.get(
                    f"{self.api}/getUpdates",
                    params={"offset": offset, "timeout": 30,
                            "allowed_updates": '["message","callback_query"]'},
                )
                updates = r.json().get("result", [])
            except Exception as e:
                log.warning("poll failed: %s", e)
                await asyncio.sleep(3)
                continue

            for u in updates:
                offset = u["update_id"] + 1
                env = await self._to_envelope(u)
                if env:
                    asyncio.create_task(sink(env))

    async def _download(self, file_id: str) -> bytes | None:
        """file_id -> bytes, via getFile. None on anything going wrong.

        A photo that will not download is a lead that still needs capturing, so
        this never raises -- the envelope just arrives without the image and the
        agent asks them to type the details instead.
        """
        try:
            r = await self._http.get(f"{self.api}/getFile", params={"file_id": file_id})
            path = r.json().get("result", {}).get("file_path")
            if not path:
                return None
            f = await self._http.get(
                f"https://api.telegram.org/file/bot{self.token}/{path}"
            )
            f.raise_for_status()
            if len(f.content) > MAX_PHOTO_BYTES:
                log.warning("photo too large (%d bytes), skipping", len(f.content))
                return None
            return f.content
        except Exception as e:
            log.warning("photo download failed: %s", e)
            return None

    async def _to_envelope(self, u: dict) -> Envelope | None:
        cb = u.get("callback_query")
        if cb:
            msg = cb.get("message", {})
            frm = cb.get("from", {})
            asyncio.create_task(self._ack_callback(cb["id"]))
            return self._build(frm, msg.get("chat", {}), cb.get("data", ""),
                               external_id=f"cb:{cb['id']}",
                               extra={"choice_id": cb.get("data", "")}, raw=u)

        msg = u.get("message")
        if not msg:
            return None
        text = msg.get("text") or msg.get("caption") or ""
        attachments: list[Attachment] = []
        if msg.get("voice"):
            attachments.append(Attachment(kind="audio", name="voice note",
                                          mime="audio/ogg"))
        if msg.get("photo"):
            # Telegram sends the same photo at several sizes, smallest first.
            # The largest is the only one an ID number is legible in.
            largest = msg["photo"][-1]
            attachments.append(Attachment(
                kind="image", name="photo", mime="image/jpeg",
                data=await self._download(largest["file_id"]),
            ))
        if msg.get("document"):
            d = msg["document"]
            attachments.append(Attachment(kind="file", name=d.get("file_name"),
                                          mime=d.get("mime_type")))
        if not text and not attachments:
            return None

        extra: dict = {}
        if msg.get("reply_to_message", {}).get("text"):
            extra["replying_to"] = msg["reply_to_message"]["text"][:300]
        return self._build(msg.get("from", {}), msg.get("chat", {}), text,
                           external_id=f"{msg['chat']['id']}:{msg['message_id']}",
                           extra=extra, attachments=attachments, raw=u)

    def _build(self, frm: dict, chat: dict, text: str, *, external_id: str,
               extra: dict, attachments: list[Attachment] | None = None,
               raw: dict | None = None) -> Envelope:
        name = " ".join(x for x in (frm.get("first_name"), frm.get("last_name")) if x)
        context = {
            "surface": "Telegram",
            "chat_type": chat.get("type"),
            "user_language": frm.get("language_code"),
            **extra,
        }
        if chat.get("type") != "private" and chat.get("title"):
            context["group_name"] = chat["title"]
        return Envelope(
            identity=Identity("telegram", str(frm.get("id")), name or None,
                              frm.get("username")),
            conversation=Conversation("telegram", str(chat.get("id")), None,
                                      chat.get("type") == "private"),
            text=text,
            capabilities=TELEGRAM,
            attachments=attachments or [],
            context=SurfaceContext({k: v for k, v in context.items() if v}),
            external_id=external_id,
            raw=raw or {},
        )

    async def _ack_callback(self, cb_id: str) -> None:
        try:
            await self._http.post(f"{self.api}/answerCallbackQuery",
                                  json={"callback_query_id": cb_id})
        except Exception:
            pass

    # --- outbound --------------------------------------------------------

    async def send(self, conv: Conversation, reply: Reply) -> str:
        last = ""
        for payload in render(reply):
            r = await self._http.post(f"{self.api}/sendMessage",
                                      json={"chat_id": conv.channel_id, **payload})
            data = r.json()
            if not data.get("ok"):
                log.warning("send failed: %s", data.get("description"))
                # last-ditch: strip formatting rather than lose the message
                r = await self._http.post(
                    f"{self.api}/sendMessage",
                    json={"chat_id": conv.channel_id, "text": reply.plain()[:4000]},
                )
                data = r.json()
            last = str(data.get("result", {}).get("message_id", ""))
        return last

    async def edit(self, conv: Conversation, message_id: str, reply: Reply) -> None:
        payload = render(reply)[0]
        payload.pop("reply_markup", None)
        try:
            await self._http.post(f"{self.api}/editMessageText",
                                  json={"chat_id": conv.channel_id,
                                        "message_id": int(message_id), **payload})
        except Exception:
            pass

    async def typing(self, conv: Conversation, on: bool = True) -> None:
        if not on:
            return
        try:
            await self._http.post(f"{self.api}/sendChatAction",
                                  json={"chat_id": conv.channel_id, "action": "typing"})
        except Exception:
            pass

    async def stop(self) -> None:
        self._running = False
        await self._http.aclose()
