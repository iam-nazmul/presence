"""The agent turn loop.

One turn per iteration: stream the assistant message, append it, execute every
tool call, append each result, repeat until the model answers without calling
anything. MAX_TURNS caps it.

Deliberately boring and deliberately ours -- a graph framework here would buy
nothing and cost a day of debugging.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

from presence.config import settings
from presence.core.envelope import Envelope
from presence.core.events import (
    AgentEvent,
    Failed,
    Final,
    NeedsConfirm,
    TextDelta,
    ThinkingDelta,
    ToolFinished,
    ToolStarted,
)
from presence.core.reply import Reply, TextBlock
from presence.providers.openai_compat import (
    ModelRouter,
    OpenAICompatProvider,
    carries_image,
)
from presence.store import db
from presence.tools import registry
from presence.tools.registry import ToolContext, ToolSpec

log = logging.getLogger("presence.agent")

MAX_TOOL_RESULT = 6000


class DefaultRuntime:
    """The reference AgentRuntime. Swap it for LangGraph et al at this seam."""

    def __init__(self, provider: Any | None = None) -> None:
        registry.load_packs()
        self.provider = provider or OpenAICompatProvider()

    # --- public ----------------------------------------------------------

    async def run(self, env: Envelope, conv_key: str,
                  principal_id: str) -> AsyncIterator[AgentEvent]:
        from presence.agent.prompts import build_messages

        messages = build_messages(env, conv_key, principal_id)
        async for ev in self._drive(env, conv_key, principal_id, messages, start_turn=0):
            yield ev

    async def resume(self, env: Envelope, conv_key: str, principal_id: str,
                     pending: dict[str, Any], approved: bool) -> AsyncIterator[AgentEvent]:
        """Continue a run that parked on a confirmation."""
        messages = pending["messages"]
        call = pending["call"]
        if approved:
            spec = registry.TOOLS.get(call["function"]["name"])
            ctx = ToolContext(principal_id, env, pending.get("run_id", "-"), conv_key)
            result = await self._execute(spec, call, ctx) if spec else "That tool no longer exists."
        else:
            result = "The user declined this action. Do not try it again; acknowledge and move on."
        messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})
        async for ev in self._drive(env, conv_key, principal_id, messages,
                                    start_turn=pending.get("turn", 0) + 1):
            yield ev

    # --- the loop --------------------------------------------------------

    async def _drive(self, env: Envelope, conv_key: str, principal_id: str,
                     messages: list[dict], start_turn: int) -> AsyncIterator[AgentEvent]:
        model = ModelRouter.for_messages(messages)
        # A local vision model reading a photograph is minutes of work, and the
        # ordinary timeout turns that into "something went wrong" after the
        # person has already waited for it.
        budget = (settings.vision_timeout_s if carries_image(messages)
                  else settings.request_timeout_s)
        run_id = db.start_run(env.id, conv_key, principal_id, env.surface,
                              self.provider.name, model)
        tools = registry.specs_for(env.trust)
        answer = ""
        turn = start_turn

        try:
            while turn < settings.max_turns:
                turn += 1
                text_parts: list[str] = []
                calls: list[dict] = []

                async for delta in self.provider.stream(messages, tools, model=model,
                                                        timeout=budget):
                    if delta.thinking:
                        yield ThinkingDelta(delta.thinking)
                    if delta.text:
                        text_parts.append(delta.text)
                        yield TextDelta(delta.text)
                    if delta.done and delta.tool_calls:
                        calls = delta.tool_calls

                answer = "".join(text_parts).strip()

                if not calls:
                    if not answer and turn < settings.max_turns:
                        # Small models sometimes return an empty turn straight after a
                        # tool result. One nudge is cheaper than a dead reply.
                        messages.append({
                            "role": "user",
                            "content": "Answer now, in your own words, using what the "
                                       "tools returned. Do not call any more tools.",
                        })
                        continue
                    db.end_run(run_id, "done", turn)
                    yield Final(_reply(answer or "I could not work that one out."))
                    return

                messages.append({
                    "role": "assistant",
                    "content": answer or None,
                    "tool_calls": calls,
                })

                # split by whether the model must wait for a human
                plan: list[tuple[dict, ToolSpec | None, registry.Decision]] = []
                for call in calls:
                    spec = registry.TOOLS.get(call["function"]["name"])
                    # A name we do not recognise is a mistake, not a permission
                    # problem -- let _execute tell the model the real tool names
                    # instead of implying it needs approval.
                    decision = (registry.decide(spec, env) if spec
                                else registry.Decision("allow"))
                    plan.append((call, spec, decision))

                parked = next(((c, s) for c, s, d in plan if d.kind == "confirm"), None)
                if parked:
                    call, spec = parked
                    db.end_run(run_id, "parked", turn,
                               pending={"messages": messages, "call": call,
                                        "turn": turn, "run_id": run_id})
                    yield NeedsConfirm(
                        call_id=call["id"],
                        name=call["function"]["name"],
                        summary=_confirm_text(spec, call),
                        choices=registry.confirm_choices(run_id, call["id"]),
                    )
                    return

                # everything else runs; reads go in parallel
                async def one(call: dict, spec: ToolSpec | None,
                              decision: registry.Decision) -> tuple[str, str]:
                    name = call["function"]["name"]
                    args = _args(call)
                    if decision.kind == "deny":
                        db.log_tool(run_id, name, args, "deny", False, decision.reason, 0)
                        return call["id"], f"Not permitted. {decision.reason}"
                    t0 = time.monotonic()
                    out = await self._execute(spec, call,
                                              ToolContext(principal_id, env, run_id, conv_key))
                    ms = int((time.monotonic() - t0) * 1000)
                    db.log_tool(run_id, name, args, "allow", True, out[:400], ms)
                    return call["id"], out

                for call, _spec, _decision in plan:
                    yield ToolStarted(call["id"], call["function"]["name"], _args(call))

                results = await asyncio.gather(*(one(c, s, d) for c, s, d in plan))

                for (call, _spec, _d), (call_id, out) in zip(plan, results, strict=True):
                    yield ToolFinished(call_id, call["function"]["name"], True, out[:200], 0)
                    messages.append({"role": "tool", "tool_call_id": call_id, "content": out})

            db.end_run(run_id, "done", turn)
            yield Final(_reply(
                (answer + "\n\n" if answer else "")
                + "I ran out of steps before finishing. That is what I have so far."
            ))
        except Exception as e:  # never let a provider hiccup kill the surface
            # Logged, not just recorded: the reply the person gets is deliberately
            # plain, and on WhatsApp it is softened further, so without this line
            # a failure leaves no trace anywhere anyone looks.
            log.exception("run %s failed on %s after turn %d (model %s)",
                          run_id, env.surface, turn, model)
            db.end_run(run_id, "failed", turn, error=f"{type(e).__name__}: {e}")
            yield Failed(_failure_text(e, env))

    # --- tool execution --------------------------------------------------

    @staticmethod
    async def _execute(spec: ToolSpec | None, call: dict, ctx: ToolContext) -> str:
        """Unknown names and bad arguments come back as readable strings.

        The model picks these names; it will get one wrong; it can recover from
        a sentence but not from an exception.
        """
        name = call["function"]["name"]
        if spec is None:
            known = ", ".join(sorted(registry.TOOLS))
            return f"There is no tool called '{name}'. Available tools: {known}."
        args = _args(call)
        if args is None:
            return f"The arguments for {name} were not valid JSON. Send them again."

        sig = inspect.signature(spec.handler)
        accepted = {k: _coerce(spec, k, v) for k, v in args.items() if k in sig.parameters}
        missing = [
            p for p, param in sig.parameters.items()
            if p != "ctx" and param.default is inspect.Parameter.empty and p not in accepted
        ]
        if missing:
            return f"{name} needs {', '.join(missing)}. Call it again with those."

        try:
            out = await spec.handler(ctx, **accepted)
        except Exception as e:
            return f"{name} failed: {type(e).__name__}: {e}"

        out = (out or "").strip()
        if not out:
            return f"{name} returned nothing."
        if len(out) > MAX_TOOL_RESULT:
            dropped = len(out) - MAX_TOOL_RESULT
            out = out[:MAX_TOOL_RESULT] + f"\n…truncated, {dropped} characters omitted."
        return out


def _coerce(spec: ToolSpec, key: str, value: Any) -> Any:
    """Nudge an argument towards the type the tool declared.

    Small models routinely send 5 where the schema says "5", or a bare string
    where a number belongs. Fixing that here keeps every tool body free of
    defensive casting.
    """
    declared = spec.parameters.get("properties", {}).get(key, {}).get("type")
    try:
        if declared == "string" and not isinstance(value, str):
            return "" if value is None else str(value)
        if declared == "integer" and not isinstance(value, int):
            return int(float(value))
        if declared == "number" and not isinstance(value, int | float):
            return float(value)
        if declared == "boolean" and not isinstance(value, bool):
            return str(value).strip().lower() in ("true", "1", "yes")
    except (TypeError, ValueError):
        return value  # let the tool report it in its own words
    return value


def _args(call: dict) -> dict[str, Any]:
    try:
        parsed = json.loads(call["function"].get("arguments") or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


class _Blanks(dict):
    """Missing key -> empty string, so an optional field never kills a template."""

    def __missing__(self, key: str) -> str:
        return ""


# Values worth confirming but not worth reprinting in a chat anyone can scroll.
_SENSITIVE = ("id_number", "password", "token", "secret", "api_key")


def _redact(key: str, value: Any) -> str:
    v = str(value)
    if any(s in key.lower() for s in _SENSITIVE) and len(v) > 4:
        return f"…{v[-4:]}"
    return v[:60]


def _confirm_text(spec: ToolSpec | None, call: dict) -> str:
    args = _args(call)
    if spec and spec.confirm_template:
        try:
            text = spec.confirm_template.format_map(_Blanks(args))
            # a template whose optional lines all blanked out leaves ragged gaps
            return "\n".join(ln for ln in text.splitlines() if ln.strip()) or text
        except (IndexError, ValueError):
            pass
    pretty = ", ".join(f"{k}={_redact(k, v)}" for k, v in args.items())
    return f"This will run {call['function']['name']}({pretty}). Go ahead?"


def _failure_text(e: Exception, env: Envelope) -> str:
    """What to say out loud about a failure.

    A timeout is not a bug the person can do anything about, but it is the one
    failure with an obvious way round it, so it gets its own sentence instead of
    the generic one -- and a photo is nearly always what caused it.
    """
    name = type(e).__name__
    if "Timeout" in name or "timed out" in str(e).lower():
        if any(a.kind == "image" for a in env.attachments):
            return ("That photo took too long to go through. Send a smaller one, or "
                    "just type the details and I will take them down.")
        return "That took too long to come back. Try me again?"
    return f"Something broke while I was working on that ({name})."


def _reply(text: str) -> Reply:
    return Reply(blocks=[TextBlock(text)])
