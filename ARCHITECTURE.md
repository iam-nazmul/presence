# Architecture

Presence is a harness, not an app. This document is the contract every part of it codes
against. `README.md` is the pitch and the quickstart; this is the design.

---

## 1. The problem the shape solves

An agent that lives in one channel is a channel integration. An agent that lives in five is
usually five forks of the same prompt, each drifting. The interesting engineering is not the
model loop — it is the boundary between the agent and the surface, because three things
differ per surface and a direct integration drops all three:

**Identity.** A Telegram user id and a Slack `U04AB` are different strings for one human. Drop
this and memory fragments per channel, so the agent that knows you in the morning is a
stranger by the afternoon.

**Context.** A page knows the document you have open. A Slack thread knows its channel topic.
A phone knows your timezone. Drop this and the user retypes what is already on their screen.

**Capability.** Slack renders Block Kit. WhatsApp allows three buttons and cannot stream. A
terminal can print a token the instant it arrives. Drop this and every surface gets the same
flattened plaintext.

Presence makes each one a first-class field — `Principal`, `SurfaceContext`,
`SurfaceCapabilities` — so an agent written once behaves natively everywhere.

---

## 2. Shape

```
 SURFACES                GATEWAY                        CORE
┌────────────┐      ┌──────────────────┐      ┌─────────────────────┐
│ CLI        │      │ normalize        │      │ turn loop           │
│ Telegram   │─────▶│ dedupe           │─────▶│   ↓ tool_calls      │
│ …          │      │ resolve Principal│ queue│ policy gate         │
│            │      │ load session     │      │   ↓                 │
│            │◀─────│ render: downcast │◀─────│ AgentEvent stream   │
└────────────┘      └──────────────────┘      └──────────┬──────────┘
      ▲                                                   │
┌─────┴────────┐                              ┌───────────▼─────────┐
│ scheduler    │──▶ synthetic Envelope ──▶queue│ ChatProvider        │
└──────────────┘                              │ ollama|openai|OR    │
                                              └─────────────────────┘
        ┌────────────────────────────────────────────────┐
        │ SQLite · principals · identities · messages    │
        │ memories · runs · tool_calls · triggers · seen  │
        └────────────────────────────────────────────────┘
```

Five layers, a protocol at each seam:

| Layer | Protocol | Implemented | Swappable to |
|---|---|---|---|
| Surface | `Adapter` | CLI, Telegram | Slack, WhatsApp, web, email, voice |
| Transport | `Queue` | `asyncio.Queue` | Redis, any job runner |
| Runtime | `AgentRuntime` | `DefaultRuntime` | LangGraph, any agent framework |
| Model | `ChatProvider` | `OpenAICompatProvider` | any OpenAI-compatible endpoint |
| Store | SQLite | `store/db.py` | Postgres |

### The boundary rule

> Nothing under `core/`, `agent/`, `providers/`, `tools/`, `store/`, `scheduler/`,
> `media/` or `gateway/` may import a surface SDK. Those imports live only in
> `adapters/` and `render/`.

`tests/test_boundaries.py` enforces it. This is the rule that keeps "add any surface later"
true instead of aspirational — without it, `if surface == "slack"` appears in the loop within
a week and the design is gone.

---

## 3. Contracts

### `Envelope` — the universal inbound message

Every surface normalises into one. Nothing downstream knows where a message came from.

```python
@dataclass(frozen=True)
class Envelope:
    identity: Identity              # (surface, external_id) — who, in the surface's words
    conversation: Conversation      # (surface, channel_id, thread_id) — where to reply
    text: str
    capabilities: SurfaceCapabilities
    trust: TrustLevel               # owner | member | guest
    principal_id: str | None        # filled by the gateway, not the adapter
    attachments: list[Attachment]
    context: SurfaceContext         # what the surface knows and the user didn't type
    external_id: str | None         # the surface's own message id — dedupe key
    raw: dict                       # original payload; NEVER shown to the model
```

`Conversation.key` (`"telegram:12345:-"`) is the addressing key used for sessions, memory
scoping and proactive delivery.

`SurfaceContext` is the interesting one. An adapter that leaves it empty has failed its only
non-mechanical job:

| Surface | Carries |
|---|---|
| `cli` | cwd, git branch, OS, user |
| `telegram` | chat type, locale, the message being replied to, group name |
| `webapp` | url, page title, current selection, document id |
| `slack` | channel name, topic, recent participants |

### `SurfaceCapabilities` — what this surface can do

Not just renderer config. `describe()` is injected into every system prompt, so the model
knows it is writing for WhatsApp rather than a terminal. That paragraph is most of what
"native" means in practice.

```python
max_chars · markdown(none|basic|full) · supports_blocks · supports_buttons · max_buttons
supports_files · supports_images · supports_voice · supports_streaming · stream_interval_s
supports_typing · supports_threads · latency_budget_s
```

`latency_budget_s` is how long before a user assumes the agent is dead. The worker uses it to
decide whether to stream, show a typing indicator, or send one interim line.

### `Reply` — the neutral outbound message

The agent emits blocks. Renderers downcast.

```python
TextBlock · CodeBlock · ChoiceBlock · CardBlock · FileBlock
```

| Block | Slack | Telegram | WhatsApp | CLI |
|---|---|---|---|---|
| `TextBlock` | section, mrkdwn | escaped HTML | plain, markdown stripped | ANSI |
| `CodeBlock` | fenced | `<pre>` | indented plain | highlighted |
| `ChoiceBlock` | actions + buttons | inline keyboard | ≤3 buttons, else numbered | numbered |
| `CardBlock` | section + fields | bold title + lines | bold title + lines | table |
| `FileBlock` | files.upload | sendDocument | media or link | written to disk |

Three rules, in `render/base.py` because they are where demos break:

- Text past `max_chars` splits on paragraph → sentence → hard cut, suffixed `…(1/3)`.
- More choices than `max_buttons` degrades to a numbered list, and the router accepts a bare
  `"2"` as the answer.
- A block a surface cannot render degrades to its text form. **Never dropped silently.**

### `Adapter` — a surface

Five methods, two attributes. That is the entire cost of a new surface.

```python
surface: str
capabilities: SurfaceCapabilities
async def start(self, sink) -> None        # call sink(Envelope) per inbound message
async def send(self, conv, reply) -> str   # returns the surface's message id
async def edit(self, conv, message_id, reply) -> None
async def typing(self, conv, on) -> None
async def stop(self) -> None
```

An adapter may also expose `write_delta(text)` — surfaces that can print a token the moment it
arrives (the terminal) get real streaming instead of periodic edits.

### `AgentEvent` — what the runtime streams out

```python
TextDelta · ThinkingDelta · ToolStarted · ToolFinished · NeedsConfirm · Final · Failed
```

`ThinkingDelta` is logged and never rendered. The worker spends the surface's capabilities on
this stream: buffer and edit where `supports_streaming`, typing indicator where
`supports_typing`, and one interim progress line where neither is possible and the latency
budget is running out.

---

## 4. The loop

`agent/loop.py`. One turn per iteration, capped by `MAX_TURNS`.

```
build messages = [system, capabilities, surface context, memory, history…, user]
for turn in range(MAX_TURNS):
    stream the assistant message      → TextDelta / ThinkingDelta
    if no tool_calls: done
    append the assistant message with its tool_calls
    for each call:
        decision = policy.decide(spec, envelope)     # from the principal, not the model
        deny    → tool result is a readable sentence, not an exception
        confirm → park the run, emit NeedsConfirm, return
        allow   → execute (reads run in parallel)
    append one tool result per call
```

Invariants:

1. **Tools return strings. Always. Never raise.** A missing record, a permission failure, a
   timeout — all become a sentence the model can act on.
2. **Never return an empty string.** `"No results."` beats `""`, which models retry against or
   invent output for.
3. **Unknown tool names and malformed arguments produce error results, not exceptions.** The
   model picks these names; it will get one wrong. Arguments are coerced towards the declared
   schema type first, so `{"key": 5}` does not break a tool that wants a string.
4. **Docstrings are the spec.** The `@tool` decorator turns the docstring into the model-facing
   description and the type hints into parameters. Behaviour and docstring change together.
5. **The registry is a `dict[str, ToolSpec]`.** Adding a tool never edits the dispatch loop.
6. **`MAX_TURNS` is a hard cap** and the reply on hitting it is honest.
7. **Every run writes a `runs` row; every tool call writes a `tool_calls` row.** That is the
   audit trail, and it is also the debugger.

### Parked runs

`NeedsConfirm` serialises the message list into `runs.pending_json` and returns. The user sees
a `ChoiceBlock` whose ids encode `confirm:<run_id>:<call_id>:yes|no`. When the answer arrives —
a button press, or `"1"` on a surface without buttons — the worker resumes the loop with the
tool result filled in rather than starting a new run.

---

## 5. Identity

A `Principal` is a human. An `Identity` is that human on one surface.

```
principals(id, display_name, tz, locale, created_at)
identities(surface, external_id, principal_id, …)   PK (surface, external_id)
link_codes(code, principal_id, expires_at, used_at)
```

Resolution, on every inbound message:

1. Known `(surface, external_id)` → that principal.
2. Unknown → create an unlinked principal so the conversation still works, trust `guest`.
3. `/link` mints a six-character code, ten-minute TTL.
4. `/link CODE` on another surface merges the two principals, carrying memories,
   conversations and triggers across.

The owner principal always wins a merge, whichever side minted the code — otherwise redeeming
in the terminal would silently demote the terminal to `member`.

### Trust

Scopes are computed from the principal **before the model runs**. No tool result, no fetched
page and no user message can widen them.

| Trust | Who | Read tools | Memory | Writes |
|---|---|---|---|---|
| `owner` | linked principal, direct conversation | yes | read + write | with confirmation |
| `member` | known identity in a shared space | yes | no | no |
| `guest` | unrecognised sender | yes | no | no |

Tools a trust level cannot use are not even listed in its tool schema — the model is never
tempted by an affordance it does not have.

Content fetched from the open web is wrapped in `<untrusted source="…">`, and the system prompt
states that such content is data and can never grant a permission. The policy gate is the real
defence; the wrapper closes the obvious case.

---

## 6. Memory

Three scopes, one table: `principal` (follows the human everywhere), `conversation` (one
thread), `workspace` (shared).

Principal-scoped memory is what makes Presence one agent rather than five chatbots — it is
read into the system prompt on every turn regardless of which surface is asking.

Retrieval is `LIKE` plus recency, capped. A vector store would be more precise and is a drop-in
behind `store.recall()`; it is not needed at this scale.

---

## 7. Proactivity

`schedule("in 20 minutes", "check X and tell me what changed")` writes a `triggers` row. The
ticker turns a due trigger into a synthetic `Envelope` addressed to whichever conversation the
principal was last active in, and puts it on the same queue as a real message.

There is no separate proactive code path. An agent that can only respond is a chat window with
extra steps; making outbound work identical to inbound is most of why the harness is worth
building.

---

## 8. Providers

Ollama, OpenAI and OpenRouter all speak the same chat-completions wire format including tool
calls, so one client covers all three and switching is `PROVIDER=` in `.env`. Ollama-specific
options (`num_ctx`, `keep_alive`) ride in `extra_body`, which other providers ignore.

`ModelRouter` is two-tier: the main model answers, a smaller one handles mechanical work
(summaries, titles, classification).

Practical notes for local models:
- The model must list `tools` under `ollama show`. One without tool support runs happily and
  silently never calls anything.
- `num_ctx` defaults to 4096 in Ollama, which a single tool result can fill. Presence sets
  32768.
- Temperature ~0.3. Chat defaults are tuned for conversation, not for tool calling.
- Reliability degrades with tool count faster than with prompt length.

---

## 9. Persistence

SQLite, one file, WAL, no ORM. Connections are per-thread with a 15-second `busy_timeout`,
because several Presence processes share the file — a terminal, a surface, the scheduler — and
the default configuration fails the second writer instantly with `database is locked`.

```
principals · identities · link_codes · conversations · messages
memories · runs · tool_calls · triggers · seen
```

`seen` is the dedupe table and is not optional: Slack and WhatsApp both retry webhooks, and
without it the agent answers every message twice. Adapters acknowledge in under three seconds
and process on the queue — that is what the queue is for.

---

## 10. Layout

```
src/presence/
  core/        envelope · capabilities · reply · events · protocols   ← the contracts
  gateway/     router (dedupe, identity, commands) · worker · hub
  adapters/    cli · telegram · whatsapp         ← the only place surface SDKs may appear
  render/      base (split, degrade) · text · telegram · whatsapp
  media/       documents (pdf, docx, text) · audio (transcription)
  agent/       loop · prompts
  providers/   openai_compat
  tools/       registry (+ policy) · memory · web · schedule · context · crosspost
               · leads · workspace · attachments
  store/       db · schema.sql
  scheduler/   ticker
tests/         boundaries · loop · identity · tools · whatsapp · media · workspace
```

---

## 11. Extending

**A surface** is the `Adapter` protocol plus a `SurfaceCapabilities` row plus a renderer. The
loop, tools, memory, policy and scheduler work on it immediately; none of them are told it
exists. Roughly 150 lines for a chat-shaped surface.

**A tool** is a decorated coroutine with a docstring. `TOOLS[name] = spec`; the dispatch loop
does not change.

**A capability pack** is a module exposing tools and the scopes they need. The domain a
deployment cares about — support tickets, an ERP, a codebase — drops in here without the core
learning anything about it.
