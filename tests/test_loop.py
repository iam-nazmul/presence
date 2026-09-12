"""The loop, against a fake provider. No model, no network, no surface."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from presence.agent.loop import DefaultRuntime
from presence.core.capabilities import CLI
from presence.core.envelope import Conversation, Envelope, Identity
from presence.core.events import Final, ToolStarted
from presence.providers.openai_compat import Delta
from presence.tools import registry


class FakeProvider:
    """Replays a scripted sequence of turns."""

    name = "fake"

    def __init__(self, turns: list[tuple[str, list[dict]]]) -> None:
        self.turns = turns
        self.seen: list[list[dict]] = []

    async def aclose(self) -> None:
        return

    async def stream(self, messages, tools, *, model=None, **opts):
        self.seen.append(list(messages))
        text, calls = self.turns.pop(0) if self.turns else ("done", [])
        for ch in text.split(" "):
            yield Delta(text=ch + " ")
        yield Delta(done=True, tool_calls=calls or None)


def call(name: str, **args: Any) -> dict:
    return {"id": f"c{name}", "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}


def envelope(text: str = "hello", trust: str = "owner") -> Envelope:
    return Envelope(
        identity=Identity("cli", "local", "tester"),
        conversation=Conversation("cli", "test", None, True),
        text=text,
        capabilities=CLI,
        trust=trust,
        principal_id="owner",
    )


async def collect(runtime: DefaultRuntime, env: Envelope) -> list:
    return [ev async for ev in runtime.run(env, "cli:test:-", "owner")]


def test_tool_call_then_answer(tmp_path, monkeypatch) -> None:
    provider = FakeProvider([
        ("Looking that up.", [call("recall", query="project")]),
        ("Here is what I found.", []),
    ])
    runtime = DefaultRuntime(provider=provider)
    events = asyncio.run(collect(runtime, envelope()))

    assert any(isinstance(e, ToolStarted) and e.name == "recall" for e in events)
    finals = [e for e in events if isinstance(e, Final)]
    assert len(finals) == 1
    assert "found" in finals[0].reply.plain()
    # the tool result was fed back to the model on the second turn
    assert any(m.get("role") == "tool" for m in provider.seen[1])


def test_two_tools_in_one_turn_both_dispatch() -> None:
    provider = FakeProvider([
        ("Checking.", [call("recall", query="a"), call("list_surfaces")]),
        ("Done.", []),
    ])
    runtime = DefaultRuntime(provider=provider)
    events = asyncio.run(collect(runtime, envelope()))
    started = [e.name for e in events if isinstance(e, ToolStarted)]
    assert started == ["recall", "list_surfaces"]
    tool_msgs = [m for m in provider.seen[1] if m.get("role") == "tool"]
    assert len(tool_msgs) == 2


def test_unknown_tool_becomes_a_readable_string_not_an_exception() -> None:
    provider = FakeProvider([
        ("Trying.", [call("delete_everything")]),
        ("Understood.", []),
    ])
    runtime = DefaultRuntime(provider=provider)
    events = asyncio.run(collect(runtime, envelope()))
    assert any(isinstance(e, Final) for e in events)
    tool_msg = [m for m in provider.seen[1] if m.get("role") == "tool"][0]
    assert "no tool called" in tool_msg["content"]


def test_max_turns_is_a_hard_cap() -> None:
    provider = FakeProvider([("Again.", [call("list_surfaces")])] * 50)
    runtime = DefaultRuntime(provider=provider)
    events = asyncio.run(collect(runtime, envelope()))
    final = [e for e in events if isinstance(e, Final)][0]
    assert "ran out of steps" in final.reply.plain()


def test_guest_cannot_write() -> None:
    registry.load_packs()
    spec = registry.TOOLS["remember"]
    assert registry.decide(spec, envelope(trust="guest")).kind == "deny"
    assert registry.decide(spec, envelope(trust="owner")).kind == "allow"


def test_guest_is_not_even_told_about_memory_tools() -> None:
    registry.load_packs()
    names = {s["function"]["name"] for s in registry.specs_for("guest")}
    assert "remember" not in names and "web_search" in names
