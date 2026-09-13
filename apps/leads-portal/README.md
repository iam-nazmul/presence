# Leads Portal

A one-file web view of the leads captured by the Presence agent over Telegram. Search,
filter and sort; reveal a masked ID number on click; view the ID or visiting card that was
photographed.

No build step, no npm, no framework. One `index.html`, Tailwind from a CDN, vanilla JS.

## Run it

**1. Start the API**, from the repository root:

```bash
uv run presence api          # API only
# or: uv run presence serve  # Telegram bot + API together
```

It listens on `http://127.0.0.1:8765`.

**2. Open the portal:**

```bash
open apps/leads-portal/index.html
```

That is enough — the page talks to the API cross-origin and the API allows it.
If your browser is strict about `file://`, serve it instead:

```bash
cd apps/leads-portal && python3 -m http.server 5173
# then open http://localhost:5173
```

## What it shows

| Column | Notes |
|---|---|
| When | Relative time, sortable |
| Name / Phone / Email / Company / Interest | As captured, from chat or read off a card |
| Email | **Unique.** One lead per address; a second capture on a known email is refused |
| ID | **Masked to the last 4 digits.** Click the row to reveal |
| Business | The tenant key — one deployment, several business lines |
| Status | `new` on capture |
| Card | Opens the uploaded photo, when there is one |
| 🗑 | Deletes the lead, after a confirmation prompt |

Search covers name, phone, email, company, interest, note and lead id. Click any column
header to sort, click again to reverse. Everything filters client-side over the loaded
array, so it is instant. The list refreshes itself every 15 seconds.

## API

Four routes:

```
GET    /api/health              -> {"ok": true, "leads": 2}
GET    /api/leads               -> [ {...}, ... ]  newest first
GET    /api/leads/<id>/photo    -> image bytes, 404 if none
DELETE /api/leads/<id>          -> {"ok": true, "id": "..."}, 404 if no such lead
```

## Notes

- **No way to create or edit a lead.** Leads are written only by the agent, and only after
  the person approves them on an in-chat confirmation card; the portal deliberately cannot
  add or edit one, so that approval step cannot be bypassed. Deleting is the exception, and
  for the same reason: a lead captured by mistake in front of an audience has to be
  removable by the person standing there. The trash button asks first, names the lead in
  the prompt, and the delete is permanent — there is no undo and no trash bin.
- **One lead per email address**, compared case- and whitespace-insensitively, so
  `Nazmul@Gmail.com` and `nazmul@gmail.com ` are the same person. The agent refuses the
  second capture and reads back the reference of the lead already on file. Leads with no
  email are not deduplicated — a phone-only lead is valid and can legitimately repeat.
- **Localhost only.** The API binds `127.0.0.1` and has no authentication, because leads
  carry phone numbers and ID card numbers. Do not put it on `0.0.0.0` without adding auth.
- Pointing at a different API: `localStorage.setItem("presenceApi", "http://host:port")`.
