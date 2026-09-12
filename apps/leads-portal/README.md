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
| ID | **Masked to the last 4 digits.** Click the row to reveal |
| Business | The tenant key — one deployment, several business lines |
| Status | `new` on capture |
| Card | Opens the uploaded photo, when there is one |

Search covers name, phone, email, company, interest, note and lead id. Click any column
header to sort, click again to reverse. Everything filters client-side over the loaded
array, so it is instant. The list refreshes itself every 15 seconds.

## API

Read-only, three routes:

```
GET /api/health              -> {"ok": true, "leads": 2}
GET /api/leads               -> [ {...}, ... ]  newest first
GET /api/leads/<id>/photo    -> image bytes, 404 if none
```

## Notes

- **Read-only by design.** Leads are created only by the agent, and only after the person
  approves them on an in-chat confirmation card. The portal deliberately has no way to add
  or edit one, so that approval step cannot be bypassed.
- **Localhost only.** The API binds `127.0.0.1` and has no authentication, because leads
  carry phone numbers and ID card numbers. Do not put it on `0.0.0.0` without adding auth.
- Pointing at a different API: `localStorage.setItem("presenceApi", "http://host:port")`.
