# apps

Front-ends that read the Presence HTTP API. One directory per app.

| App | What it is |
|---|---|
| [`leads-portal`](leads-portal) | Browse captured leads — search, filter, sort, view the uploaded card |

These are clients, not part of the agent. They talk to the API in
`src/presence/api/server.py` over HTTP and share nothing else with it, so an app can be
opened, rewritten or thrown away without touching the agent.

Conventions for the next one:

- **Read-only.** Leads are written by the agent only, and only after the person approves
  them on an in-chat confirmation card. An app that writes would route around that.
- **No build step** unless it genuinely earns one. `leads-portal` is a single `index.html`
  with Tailwind from a CDN; it needs no `npm install` and cannot break on a stale lockfile.
- **Assume localhost.** The API binds `127.0.0.1` and has no auth, because leads carry phone
  numbers and ID card numbers.
