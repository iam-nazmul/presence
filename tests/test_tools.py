"""Tool invariants.

A tool that raises kills a turn. A tool that returns "" makes the model invent
output. Both are enforced here rather than left to reviewer discipline.
"""

from __future__ import annotations

import asyncio
import inspect
import json

import pytest

from presence.core.capabilities import CLI
from presence.core.envelope import Conversation, Envelope, Identity
from presence.tools import registry
from presence.tools.registry import ToolContext

registry.load_packs()
ALL_TOOLS = sorted(registry.TOOLS)


def ctx() -> ToolContext:
    env = Envelope(
        identity=Identity("cli", "local", "tester"),
        conversation=Conversation("cli", "test", None, True),
        text="",
        capabilities=CLI,
        trust="owner",
        principal_id="owner",
    )
    return ToolContext("owner", env, "run_test", "cli:test:-")


@pytest.mark.parametrize("name", ALL_TOOLS)
def test_docstring_is_the_spec(name: str) -> None:
    spec = registry.TOOLS[name]
    assert spec.description, f"{name} has no docstring, so the model gets no description"
    assert len(spec.description) > 30, f"{name}'s description is too thin to be useful"
    assert spec.parameters["type"] == "object"


@pytest.mark.parametrize("name", ALL_TOOLS)
def test_junk_arguments_return_a_string_and_never_raise(name: str, tmp_path, monkeypatch) -> None:
    """Models send 5 where the schema says "5", and omit required arguments.

    Every one of those must come back as a sentence the model can act on.
    """
    from presence.agent.loop import DefaultRuntime
    from presence.config import settings
    from presence.store import db

    monkeypatch.setattr(settings, "db_path", str(tmp_path / f"{name}.db"))
    db._local.__dict__.pop("conn", None)
    db.init()

    spec = registry.TOOLS[name]
    required = [
        p for p, param in inspect.signature(spec.handler).parameters.items()
        if p != "ctx" and param.default is inspect.Parameter.empty
    ]

    for junk in ({p: 0 for p in required}, {p: None for p in required}, {}):
        call = {"id": "c1", "type": "function",
                "function": {"name": name, "arguments": json.dumps(junk)}}
        out = asyncio.run(DefaultRuntime._execute(spec, call, ctx()))
        assert isinstance(out, str), f"{name}{junk} returned {type(out)}"
        assert out.strip(), f"{name}{junk} returned an empty string"

    db._local.__dict__.pop("conn", None)


def test_unknown_tool_name_lists_the_real_ones() -> None:
    from presence.agent.loop import DefaultRuntime

    call = {"id": "c1", "type": "function",
            "function": {"name": "drop_database", "arguments": "{}"}}
    out = asyncio.run(DefaultRuntime._execute(None, call, ctx()))
    assert "no tool called" in out and "recall" in out


def test_malformed_json_arguments_do_not_raise() -> None:
    from presence.agent.loop import DefaultRuntime

    spec = registry.TOOLS["recall"]
    call = {"id": "c1", "type": "function",
            "function": {"name": "recall", "arguments": "{not json at all"}}
    out = asyncio.run(DefaultRuntime._execute(spec, call, ctx()))
    assert isinstance(out, str) and out.strip()


def test_every_tool_declares_a_risk_level() -> None:
    for name, spec in registry.TOOLS.items():
        assert spec.risk in ("read", "write", "external", "destructive"), name


def test_write_tools_are_gated_by_a_scope() -> None:
    """A write with no scope would be available to an anonymous stranger."""
    for name, spec in registry.TOOLS.items():
        if spec.risk in ("write", "destructive"):
            assert spec.scopes, f"{name} can change things but declares no scope"
