"""Tool registry and the policy gate.

Adding a tool means writing a function with a docstring and decorating it. The
dispatch loop never changes.

Hard rules for every tool body:
  * return a string, always; never raise
  * never return an empty string -- "No results." beats ""
  * trim output and say what was omitted
"""

from __future__ import annotations

import inspect
import textwrap
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, get_type_hints

from presence.core.envelope import Envelope
from presence.core.reply import Choice

Risk = Literal["read", "write", "external", "destructive"]
Handler = Callable[..., Awaitable[str]]

_JSON_TYPES = {str: "string", int: "integer", float: "number", bool: "boolean",
               list: "array", dict: "object"}


@dataclass
class ToolContext:
    principal_id: str
    envelope: Envelope
    run_id: str
    conv_key: str


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    scopes: frozenset[str]
    risk: Risk
    handler: Callable[..., Awaitable[str]]
    confirm_template: str | None = None

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


TOOLS: dict[str, ToolSpec] = {}


def tool(*, risk: Risk = "read", scopes: set[str] | None = None,
         confirm: str | None = None) -> Callable[[Handler], Handler]:
    """Register a coroutine as a tool. The docstring IS the model-facing spec."""

    def wrap(fn: Callable[..., Awaitable[str]]) -> Callable[..., Awaitable[str]]:
        sig = inspect.signature(fn)
        hints = get_type_hints(fn)
        props: dict[str, Any] = {}
        required: list[str] = []
        for pname, param in sig.parameters.items():
            if pname == "ctx":
                continue
            ann = hints.get(pname, str)
            base = getattr(ann, "__origin__", ann)
            props[pname] = {"type": _JSON_TYPES.get(base, "string")}
            if param.default is inspect.Parameter.empty:
                required.append(pname)
        doc = textwrap.dedent(fn.__doc__ or "").strip()
        spec = ToolSpec(
            name=fn.__name__,
            description=doc,
            parameters={"type": "object", "properties": props, "required": required},
            scopes=frozenset(scopes or set()),
            risk=risk,
            handler=fn,
            confirm_template=confirm,
        )
        TOOLS[spec.name] = spec
        return fn

    return wrap


# --- policy ---------------------------------------------------------------


@dataclass(frozen=True)
class Decision:
    kind: Literal["allow", "confirm", "deny"]
    reason: str = ""


# Scopes granted per trust level. Computed from the principal BEFORE the model
# runs -- no tool result and no fetched page can widen them.
# 'files' and 'shell' reach the owner's actual machine, so they stop at the
# owner. A member or a guest is never even shown that those tools exist --
# specs_for() filters them out of the schema list before the model sees it.
SCOPES_BY_TRUST: dict[str, set[str]] = {
    "owner": {"web", "memory", "schedule", "crosspost", "context", "lead",
              "files", "shell"},
    "member": {"web", "context", "lead"},
    "guest": {"web", "context", "lead"},
}

# Risks a trust level may execute without a human pressing a button.
_AUTO_OK: dict[str, set[str]] = {
    "owner": {"read", "external", "write"},
    "member": {"read", "external"},
    "guest": {"read"},
}

# Tools an owner never has to confirm -- confirming every memory write kills the
# pace of a conversation.
PREAPPROVED = {"remember", "recall", "forget", "list_schedules", "read_context", "schedule",
               "list_dir", "read_file", "which"}

# Tools any trust level may run, provided the human confirms in chat first.
# Lead capture is the one write a stranger is *supposed* to make: the person
# filling in the form is by definition not the owner, so the usual
# "only the owner may change things" rule would reject every real lead. The
# safety here is the confirmation step, not the trust level -- they are shown
# exactly what will be stored and have to approve it.
CONFIRM_ANY = {"save_lead"}


def scopes_for(trust: str) -> set[str]:
    return set(SCOPES_BY_TRUST.get(trust, SCOPES_BY_TRUST["guest"]))


def decide(spec: ToolSpec, envelope: Envelope) -> Decision:
    granted = scopes_for(envelope.trust)
    missing = spec.scopes - granted
    if missing:
        return Decision(
            "deny",
            f"'{spec.name}' needs the {', '.join(sorted(missing))} permission, which this "
            f"conversation does not have. Tell the user to ask the owner in a direct message.",
        )
    if spec.name in PREAPPROVED and envelope.trust == "owner":
        return Decision("allow")
    if spec.name in CONFIRM_ANY:
        return Decision("confirm")
    if spec.risk in _AUTO_OK.get(envelope.trust, set()):
        return Decision("allow")
    if envelope.trust == "owner":
        return Decision("confirm")
    return Decision(
        "deny",
        f"'{spec.name}' changes things, and only the linked owner can approve that here.",
    )


def specs_for(trust: str) -> list[dict[str, Any]]:
    """The tool schemas this trust level is even told about."""
    granted = scopes_for(trust)
    return [s.schema() for s in TOOLS.values() if not (s.scopes - granted)]


def confirm_choices(run_id: str, call_id: str) -> list[Choice]:
    return [
        Choice(id=f"confirm:{run_id}:{call_id}:yes", label="Yes, do it", style="primary"),
        Choice(id=f"confirm:{run_id}:{call_id}:no", label="No, cancel"),
    ]


def load_packs() -> None:
    """Import the tool packs. A pack is any module that registers into TOOLS."""
    from presence.tools import (  # noqa: F401
        context,
        crosspost,
        leads,
        memory,
        schedule,
        web,
        workspace,
    )
