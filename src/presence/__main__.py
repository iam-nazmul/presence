"""Entry point.

  presence chat                 talk to it in the terminal
  presence -p "..."             one shot
  presence telegram             run the Telegram surface
  presence serve                every configured surface at once
  presence doctor               check the model, the tokens and the database
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from presence.config import settings
from presence.gateway import hub
from presence.gateway.router import ingest
from presence.gateway.worker import run_worker
from presence.scheduler.ticker import run_scheduler
from presence.store import db


def _log(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(name)-20s %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)


async def _run(surfaces: list[str], once: str | None = None) -> None:
    db.init()
    tasks: list[asyncio.Task] = []

    # The portal API rides along in this process so a demo needs one terminal,
    # not two. It is a daemon thread, so it dies with us.
    if settings.api_port > 0 and not once:
        from presence.api.server import serve_forever

        serve_forever()

    if "cli" in surfaces:
        from presence.adapters.cli import CLIAdapter

        hub.register(CLIAdapter(once=once))
    if "telegram" in surfaces and settings.telegram_token:
        from presence.adapters.telegram import TelegramAdapter

        hub.register(TelegramAdapter())

    if not hub.ADAPTERS:
        print("No surfaces configured. Set TELEGRAM_BOT_TOKEN or use `presence chat`.")
        return

    from presence.agent.loop import DefaultRuntime

    runtime = DefaultRuntime()
    tasks.append(asyncio.create_task(run_worker(runtime)))
    tasks.append(asyncio.create_task(run_scheduler()))
    for adapter in hub.ADAPTERS.values():
        tasks.append(asyncio.create_task(adapter.start(ingest)))

    try:
        if once:
            # let the one-shot land, then drain the queue and exit
            from presence.gateway.router import QUEUE

            await asyncio.sleep(0.3)
            await QUEUE.join()
        else:
            await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        pass
    finally:
        for t in tasks:
            t.cancel()
        for adapter in hub.ADAPTERS.values():
            await adapter.stop()
        await runtime.provider.aclose()
        await asyncio.sleep(0.05)


async def _doctor() -> None:
    from presence.providers.openai_compat import OpenAICompatProvider
    from presence.tools import registry

    registry.load_packs()
    print(f"provider   {settings.provider} → {settings.base_url}")
    print(f"model      {settings.model_main}  (fast: {settings.model_fast})")
    print(f"database   {settings.db_path}")
    print(f"tools      {len(registry.TOOLS)}: {', '.join(sorted(registry.TOOLS))}")
    print(f"telegram   {'token set' if settings.telegram_token else 'MISSING'}")
    print(f"owner ids  {', '.join(settings.owner_identities)}")

    print("\nchecking tool calling…")
    provider = OpenAICompatProvider()
    calls = None
    try:
        async for d in provider.stream(
            [{"role": "user", "content": "Search the web for the current Bitcoin price."}],
            registry.specs_for("owner"),
            model=settings.model_main,
        ):
            if d.done:
                calls = d.tool_calls
        if calls:
            print(f"  OK — model called {calls[0]['function']['name']}")
        else:
            print("  WARNING — model answered without calling a tool.")
            print("  Check `ollama show <model> | grep -A6 Capabilities` lists `tools`.")
    except Exception as e:
        print(f"  FAILED — {type(e).__name__}: {e}")
        print("  Is `ollama serve` running?")
    finally:
        # Close the client while the loop is still alive. Left to the loop's
        # asyncgen shutdown hook instead, httpcore's half-read SSE body
        # generators tear down mid-GeneratorExit and print a wall of
        # "generator didn't stop after athrow()" -- which makes a perfectly
        # healthy backend look broken, in the one command you run to check it.
        await provider.aclose()


async def _api() -> None:
    """The portal API on its own, with no surfaces and no model."""
    from presence.api.server import serve_forever

    db.init()
    serve_forever()
    # flush: this process then sleeps for an hour at a time, so buffered output
    # redirected to a file would not appear until it was killed.
    print(f"Leads API   http://{settings.api_host}:{settings.api_port}/api/leads", flush=True)
    print(f"Leads in db {db.count_leads()}", flush=True)
    print("Open apps/leads-portal/index.html to browse them. Ctrl-C to stop.", flush=True)
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass


def main() -> None:
    p = argparse.ArgumentParser(prog="presence", description="One agent, every surface.")
    p.add_argument("command", nargs="?", default="chat",
                   choices=["chat", "telegram", "serve", "doctor", "api"])
    p.add_argument("-p", "--prompt", help="one-shot message, then exit")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()
    _log(args.verbose)

    if args.command == "doctor":
        asyncio.run(_doctor())
        return

    if args.command == "api":
        asyncio.run(_api())
        return

    surfaces = {
        "chat": ["cli"],
        "telegram": ["telegram"],
        "serve": ["cli", "telegram"],
    }[args.command]

    try:
        asyncio.run(_run(surfaces, once=args.prompt))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
