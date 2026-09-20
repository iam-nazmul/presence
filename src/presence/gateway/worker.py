"""The worker. Queue in, delivered reply out.

This is where the surface's capabilities actually get spent: whether to stream
by editing a message, whether to show a typing indicator, or whether to send one
interim line because the surface cannot do either and the user is about to
assume the agent is dead.
"""

from __future__ import annotations

import logging
import time

from presence.agent.loop import DefaultRuntime
from presence.agent.prompts import said
from presence.core.envelope import Envelope
from presence.core.events import (
    Failed,
    Final,
    NeedsConfirm,
    TextDelta,
    ThinkingDelta,
    ToolFinished,
    ToolStarted,
)
from presence.core.reply import ChoiceBlock, Reply, TextBlock
from presence.gateway import hub
from presence.gateway.router import QUEUE
from presence.media import read_attachments
from presence.store import db

log = logging.getLogger("presence.worker")


class Presenter:
    """Turns an AgentEvent stream into whatever this surface can show."""

    def __init__(self, env: Envelope) -> None:
        self.env = env
        self.caps = env.capabilities
        self.conv = env.conversation
        self.adapter = hub.get(env.surface)
        self.buffer = ""
        self.message_id: str | None = None
        self.last_push = 0.0
        self.t0 = time.monotonic()
        self.progress_sent = False

    async def on(self, ev) -> None:
        if isinstance(ev, ThinkingDelta):
            return  # logged by the provider, never shown

        if isinstance(ev, TextDelta):
            self.buffer += ev.text
            await self._maybe_push()

        elif isinstance(ev, ToolStarted):
            if self.caps.supports_typing and self.adapter:
                await self.adapter.typing(self.conv, True)
            note = getattr(self.adapter, "note", None)
            if note:
                note(ev.human())
            await self._maybe_progress(ev.human())

        elif isinstance(ev, ToolFinished):
            if self.caps.supports_typing and self.adapter:
                await self.adapter.typing(self.conv, True)

    async def _maybe_push(self) -> None:
        """Edit-streaming, rate limited to the surface's tolerance."""
        if not (self.caps.supports_streaming and self.adapter):
            return
        if self.caps.stream_interval_s <= 0:
            # surfaces that can print a token the instant it arrives
            writer = getattr(self.adapter, "write_delta", None)
            if writer:
                writer(self.buffer)
                self.buffer = ""
            return
        now = time.monotonic()
        if now - self.last_push < self.caps.stream_interval_s or len(self.buffer) < 40:
            return
        self.last_push = now
        partial = Reply(blocks=[TextBlock(self.buffer + " …")])
        try:
            if self.message_id is None:
                self.message_id = await self.adapter.send(self.conv, partial)
            else:
                await self.adapter.edit(self.conv, self.message_id, partial)
        except Exception as e:
            log.debug("stream push failed: %s", e)

    async def _maybe_progress(self, line: str) -> None:
        """For surfaces that cannot stream and cannot show typing."""
        if self.caps.supports_streaming or self.caps.supports_typing:
            return
        if self.progress_sent or time.monotonic() - self.t0 < self.caps.latency_budget_s * 0.5:
            return
        self.progress_sent = True
        if self.adapter:
            await self.adapter.send(self.conv, Reply.text(line))

    async def finish(self, reply: Reply) -> None:
        if not self.adapter:
            return
        if self.message_id and self.caps.supports_streaming:
            await self.adapter.edit(self.conv, self.message_id, reply)
            if any(isinstance(b, ChoiceBlock) for b in reply.blocks):
                await self.adapter.send(self.conv, Reply(
                    blocks=[b for b in reply.blocks if isinstance(b, ChoiceBlock)]
                ))
        else:
            await self.adapter.send(self.conv, reply)


async def handle(env: Envelope, runtime: DefaultRuntime) -> None:
    conv_key = env.conversation.key
    principal_id = env.principal_id or "owner"
    presenter = Presenter(env)

    decision = _confirmation_answer(env, conv_key)
    if decision is not None:
        pending, approved = decision
        stream = runtime.resume(env, conv_key, principal_id, pending, approved)
    else:
        # Read the files and voice notes first, so the model is answering what
        # they sent rather than the fact that they sent something. Reading a PDF
        # or reaching a transcriber takes seconds, which is why this is here and
        # not in ingest -- the typing indicator is already up by now.
        if any(a.data and a.kind in ("file", "audio") for a in env.attachments):
            if presenter.caps.supports_typing and presenter.adapter:
                await presenter.adapter.typing(env.conversation, True)
            env = await read_attachments(env)
            presenter.env = env
        db.add_message(conv_key, "user", {"role": "user", "content": said(env)})
        stream = runtime.run(env, conv_key, principal_id)

    final: Reply | None = None
    try:
        async for ev in stream:
            if isinstance(ev, Final):
                final = ev.reply
            elif isinstance(ev, NeedsConfirm):
                final = Reply(blocks=[
                    TextBlock(ev.summary),
                    ChoiceBlock(prompt="", choices=ev.choices),
                ])
            elif isinstance(ev, Failed):
                final = Reply.error(ev.message)
            else:
                await presenter.on(ev)
    except Exception as e:
        log.exception("worker failed")
        final = Reply.error(f"I hit an internal error ({type(e).__name__}).")

    if final is None:
        final = Reply.error("I did not manage to produce an answer.")

    db.add_message(conv_key, "assistant", {"role": "assistant", "content": final.plain()})
    await presenter.finish(final)


# What agreement and refusal actually look like when nobody was shown a button.
# WhatsApp is the reason this list is long: the numbered prompt is not rendered
# there, so the answer arrives as whatever the person would have said out loud,
# in English or in Banglish. Anything not on either list falls through and is
# answered as an ordinary message, which is the safe way to be wrong -- the
# parked call stays parked rather than running on a maybe.
YES = {
    "1", "yes", "y", "yeah", "yep", "yup", "yes please", "sure", "ok", "okay",
    "k", "fine", "do it", "go ahead", "go", "please do", "send it", "save it",
    "confirm", "confirmed", "correct", "right", "haan", "han", "ha", "hae",
    "ji", "jee", "accha", "thik ache", "thik", "হ্যাঁ", "হ্যা", "জি", "ঠিক আছে",
}
NO = {
    "2", "no", "n", "nope", "nah", "na", "not now", "later", "cancel", "stop",
    "don't", "dont", "do not", "no thanks", "leave it", "skip", "na thak",
    "thak", "না", "নাহ", "থাক",
}


def _confirmation_answer(env: Envelope, conv_key: str) -> tuple[dict, bool] | None:
    """Was this message an answer to a parked confirmation?

    Accepts a button callback (confirm:<run>:<call>:yes), a bare '1' / '2'
    against the numbered fallback, or the plain words people use instead.
    """
    import json

    row = db.parked_run(conv_key)
    if row is None or not row["pending_json"]:
        return None

    choice_id = env.context.data.get("choice_id", "")
    # Texted answers arrive with the punctuation and casing people type.
    text = env.text.strip().lower().strip(" .!?।")

    if choice_id.startswith("confirm:"):
        approved = choice_id.endswith(":yes")
    elif text in YES:
        approved = True
    elif text in NO:
        approved = False
    else:
        return None

    return json.loads(row["pending_json"]), approved


async def run_worker(runtime: DefaultRuntime | None = None) -> None:
    runtime = runtime or DefaultRuntime()
    log.info("worker ready")
    while True:
        env = await QUEUE.get()
        try:
            await handle(env, runtime)
        except Exception:
            log.exception("unhandled in worker")
        finally:
            QUEUE.task_done()
