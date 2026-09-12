"""Terminal adapter. The dev harness -- and the first beat of the demo.

Keeps the whole core testable without a single channel token.
"""

from __future__ import annotations

import asyncio
import os
import subprocess

from rich.console import Console
from rich.markdown import Markdown

from presence.core.capabilities import CLI
from presence.core.envelope import Conversation, Envelope, Identity, SurfaceContext
from presence.core.protocols import Sink
from presence.core.reply import ChoiceBlock, Reply
from presence.render.base import flatten

console = Console()


def _git_branch() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                             capture_output=True, text=True, timeout=1)
        return out.stdout.strip() or None
    except Exception:
        return None


class CLIAdapter:
    surface = "cli"
    capabilities = CLI

    def __init__(self, once: str | None = None) -> None:
        self.once = once
        self.streamed = ""
        self._open = False

    async def start(self, sink: Sink) -> None:
        conv = Conversation("cli", "local", None, True)
        ident = Identity("cli", "local", os.getenv("USER") or "you")
        ctx = SurfaceContext({
            "surface": "a terminal",
            "cwd": os.getcwd(),
            "git_branch": _git_branch(),
            "os": os.uname().sysname,
            "user": os.getenv("USER"),
        })

        if self.once:
            await sink(self._env(ident, conv, ctx, self.once))
            return

        console.print("[dim]presence · type a message, /quit to leave[/dim]\n")
        loop = asyncio.get_running_loop()
        while True:
            try:
                line = await loop.run_in_executor(None, lambda: input("› "))
            except (EOFError, KeyboardInterrupt):
                console.print()
                return
            line = line.strip()
            if line in ("/quit", "/exit"):
                return
            if not line:
                continue
            await sink(self._env(ident, conv, ctx, line))
            await asyncio.sleep(0.05)

    def _env(self, ident: Identity, conv: Conversation, ctx: SurfaceContext,
             text: str) -> Envelope:
        import uuid

        return Envelope(identity=ident, conversation=conv, text=text,
                        capabilities=CLI, context=ctx,
                        external_id=uuid.uuid4().hex)

    # --- outbound --------------------------------------------------------

    def write_delta(self, text: str) -> None:
        """Token streaming. The CLI is the one surface that can do it properly."""
        if not self._open:
            console.print("[bold cyan]presence[/bold cyan] ", end="")
            self._open = True
        console.print(text, end="", markup=False, highlight=False)
        self.streamed += text

    def note(self, text: str) -> None:
        if self._open:
            console.print()
            self._open = False
        console.print(f"[dim]  · {text}[/dim]")

    async def send(self, conv: Conversation, reply: Reply) -> str:
        body = flatten(reply, CLI)
        if self._open:
            console.print()
            self._open = False
        # Whatever already went out as tokens must not be printed a second time.
        def squash(s: str) -> str:
            return " ".join(s.split())

        already = squash(self.streamed)
        remainder = "" if already and already.endswith(squash(body)) else body
        if remainder:
            console.print(Markdown(remainder))
        for b in reply.blocks:
            if isinstance(b, ChoiceBlock):
                for i, c in enumerate(b.choices, 1):
                    console.print(f"  [bold]{i}[/bold]. {c.label}")
                console.print("[dim]  reply with a number[/dim]")
        console.print()
        self.streamed = ""
        return "cli"

    async def edit(self, conv: Conversation, message_id: str, reply: Reply) -> None:
        return

    async def typing(self, conv: Conversation, on: bool = True) -> None:
        return

    async def stop(self) -> None:
        return
