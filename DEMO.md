# Demo — 2 minutes

Prep and script for the hackathon video. Two minutes is roughly **280 spoken words**, so
every line below is budgeted. Read it once, then talk normally — do not recite it.

The through-line: **the form is dead. The conversation is the form.**

---

## Before you record

```bash
uv run presence doctor        # must print: OK — model called ...
uv run presence serve         # Telegram + API in one process
open apps/leads-portal/index.html
```

Checklist:

- [ ] `doctor` says `OK — model called …`. If it warns instead, the model is not tool-calling
      and nothing else in this demo works.
- [ ] Telegram open on the phone, chat with **@softife_bot** already scrolled to the bottom.
- [ ] A visiting card or ID card in frame, ready to photograph.
- [ ] Portal open in a browser window, showing 2–3 existing leads so it does not look empty.
- [ ] One rehearsal capture done and deleted — the first model call of a session is slowest.
- [ ] Screen recording at phone + laptop side by side, if you can. The cut between them is
      the whole point.

**ID numbers are masked in the portal, but the Telegram chat shows what you send it.** Use a
prop card, not your own.

---

## The script

### 0:00 – 0:15 · The problem

> On screen: a normal Telegram chat.

"Every business that captures leads ends up with the same thing — a web form nobody fills
in, or a salesperson typing names off a card at the end of the day.
This is the same job, done in the place the customer already is."

### 0:15 – 0:50 · Capture

> On screen: phone. Send a message, then photograph a visiting card.

"I message the bot like a person, not a form."

*(send)* — `Hi, I'm interested in your bulk cement supply.`

"It asks for what it needs. And when I'd rather not type —"

*(send the photo of the card)*

"— I just send the card. It reads the name, the phone, the company straight off the image
and fills the lead in for me."

### 0:50 – 1:10 · The approval — *the important part*

> On screen: the confirmation card with Yes / No buttons.

"Before anything is stored, it shows me exactly what it's about to save, and waits.

Nothing reaches the database without a human pressing that button. An agent that reads a
photograph and writes to your CRM unsupervised is not a product, it's an incident."

*(tap Yes)* → reference id comes back.

### 1:10 – 1:30 · The portal

> On screen: laptop. The new lead appears.

"The lead is already in the portal — search it, sort it, filter by business line.
The ID number is masked. The card it was read from is one click away."

*(click a row to reveal; open the photo)*

### 1:30 – 1:50 · Architecture

> On screen: the diagram below, or the repo tree.

"Underneath, the chat surface is a thin adapter. Identity, context and capability are
resolved once in a gateway, then a single agent loop runs against a tool registry.
Telegram is about 150 lines. Adding WhatsApp or a web widget is the same 150 lines —
the lead logic doesn't change at all."

### 1:50 – 2:00 · Where it goes

"Lead capture is the first business line, not the product. The product is the boundary:
one agent, any surface, any workflow behind it — with a human in the loop on every write."

---

## Architecture, in one breath

```
 Telegram / CLI  ──▶  GATEWAY  ──▶  AGENT LOOP  ──▶  TOOLS  ──▶  SQLite
  (adapter,            identity        turn +          policy       leads,
   ~150 lines)         context         tool calls      gate         uploads,
                       capability                        │          memory
                       dedupe                            ▼
                                                  human confirms
                                                   in the chat
                                                         │
                                                         ▼
                                              READ-ONLY HTTP API ──▶ apps/leads-portal
```

Four points worth saying out loud if asked:

1. **The surface is an adapter, not the app.** `adapters/telegram.py` normalises a Telegram
   update into an `Envelope` and renders replies back down to what Telegram can show.
   Everything after that is surface-agnostic.
2. **The policy gate decides before the model runs.** Scopes are computed from the
   principal's trust level, so no tool result and no photographed text can widen them.
3. **Lead capture is the one write a stranger is meant to make.** Guests cannot change
   anything else — but they *can* file a lead, because the confirmation button, not the
   trust level, is what makes it safe.
4. **The portal is read-only by design.** If it could write, it would be a second path
   around the approval step.

---

## The business logic, precisely

| Step | What happens | Where |
|---|---|---|
| Message arrives | Normalised to an `Envelope`; any photo is parked against the conversation | `gateway/router.py` |
| Agent runs | Collects name + phone/email + interest conversationally; reads an uploaded card | `agent/loop.py`, `agent/prompts.py` |
| `save_lead` called | Refuses without a name **and** a phone or email | `tools/leads.py` |
| Policy gate | Returns `confirm` for any trust level — always a button | `tools/registry.py` |
| Human taps Yes | Lead written, reference id returned | `store/db.py` |
| Portal reads | `GET /api/leads`, newest first | `api/server.py` |

`business` is the tenant key on every lead. One deployment, many business lines later —
a parameter, not a migration.

**Why the photo is parked on arrival:** `save_lead` runs *after* the button press, and a
button press carries no attachment. Storing the bytes when the message lands is what makes
"send a card, approve it later" work at all.

---

## Future goals

Say one or two of these, not all five.

- **More business lines.** `business` is already a column. Support, bookings, complaints,
  applications — same loop, different tools, no new plumbing.
- **More surfaces.** WhatsApp and a web widget are adapters, not rewrites. The lead logic
  is untouched by either.
- **Write-back to real systems.** ERPNext, HubSpot, a Google Sheet — a lead becomes a
  record where the business already works, still gated by the same confirmation.
- **Follow-up that starts itself.** The scheduler exists. An agent that re-contacts a lead
  after three days is a trigger, not a feature.
- **Reviewer workflow in the portal.** Assign, qualify, mark won or lost — the one place
  writes might be worth adding, with its own approval.

---

## If it breaks on camera

| Symptom | Cause | Say / do |
|---|---|---|
| Bot silent | worker died, or no network | Check the terminal running `serve` first — a dead worker looks exactly like a broken hook |
| Replies but never saves | model is not tool-calling | Run `doctor`; switch `MODEL_MAIN` back to a known-good model |
| Reads the card wrong | glare, angle | Let it happen and correct it in chat — *"that's exactly why there's a confirmation step"* |
| Portal empty, red dot | API not running | `uv run presence api` — the portal tells you this on screen |
| Photo button missing | lead predates the upload fix | Capture a fresh one |

Rehearse once end-to-end. The first model call of a session is always the slowest, and you
do not want that pause in the take.
