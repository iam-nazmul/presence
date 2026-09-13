"""HTTP API over the lead store, for the portal.

Deliberately the standard library. This serves a handful of routes off a SQLite
table; a web framework would add a dependency tree and a second way to run the
process in exchange for nothing.

A *threaded* server is the right shape here rather than an accident: db.conn()
already keeps one connection per thread with WAL and a busy_timeout, precisely
so several threads and processes can share the file. The handler threads land
in that design instead of fighting it.

No route here *creates* a lead. The agent's confirmation card stays the only
path that writes one, so the human approval step that makes the whole thing
trustworthy cannot be bypassed. DELETE is the one exception to read-only, and
in the same spirit: a lead captured by mistake in front of an audience has to
be removable by the person standing there, and the portal asks them to confirm
before it sends the request.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from presence.config import settings
from presence.store import db

log = logging.getLogger("presence.api")

# High enough that the portal gets everything and never has to paginate, low
# enough that a runaway table cannot exhaust memory.
MAX_LEADS = 1000

_PHOTO = re.compile(r"^/api/leads/([A-Za-z0-9_-]{1,64})/photo$")
_LEAD = re.compile(r"^/api/leads/([A-Za-z0-9_-]{1,64})$")
# Newer rows store a real MIME type; the earliest ones stored a bare kind.
_KIND = {"image": "image/jpeg", "file": "application/octet-stream"}


def _content_type(stored: str) -> str:
    return stored if "/" in stored else _KIND.get(stored, "application/octet-stream")


class Handler(BaseHTTPRequestHandler):
    server_version = "presence-api"
    _head_only = False

    # --- plumbing --------------------------------------------------------

    def log_message(self, fmt: str, *args: Any) -> None:
        """BaseHTTPRequestHandler writes to stderr by default, which would
        interleave with the agent's own output. Route it through logging."""
        log.debug("%s - %s", self.address_string(), fmt % args)

    def _headers(self, code: int, ctype: str, length: int) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        # The portal is a separate origin -- a file:// page sends Origin: null,
        # and a python -m http.server one sends a different port. Safe to allow
        # broadly because the socket is bound to loopback.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _json(self, payload: Any, code: int = 200) -> None:
        body = json.dumps(payload, default=str).encode()
        self._headers(code, "application/json; charset=utf-8", len(body))
        if not self._head_only:
            self.wfile.write(body)

    def _bytes(self, blob: bytes, ctype: str) -> None:
        self._headers(200, ctype, len(blob))
        if not self._head_only:
            self.wfile.write(blob)

    def do_HEAD(self) -> None:  # noqa: N802  (stdlib naming)
        """Same headers as GET, no body. Curl and uptime checks reach for it."""
        self._head_only = True
        try:
            self.do_GET()
        finally:
            self._head_only = False

    def do_OPTIONS(self) -> None:  # noqa: N802  (stdlib naming)
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, DELETE, OPTIONS")
        self.end_headers()

    # --- routes ----------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802  (stdlib naming)
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        try:
            if path in ("/api/health", "/health"):
                return self._json({"ok": True, "leads": db.count_leads()})

            if path == "/api/leads":
                rows = db.list_leads(None, MAX_LEADS)
                return self._json([dict(r) for r in rows])

            shot = _PHOTO.match(path)
            if shot:
                found = db.lead_photo(shot.group(1))
                if found is None:
                    return self._json({"error": "That lead has no photo."}, 404)
                blob, kind = found
                return self._bytes(blob, _content_type(kind))

            self._json({"error": f"No route for {path}."}, 404)
        except BrokenPipeError:
            pass  # portal navigated away mid-response; nothing to report
        except Exception as e:
            # Never let the stdlib render an HTML traceback at a JSON client.
            log.exception("api request failed: %s", path)
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    def do_DELETE(self) -> None:  # noqa: N802  (stdlib naming)
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        try:
            one = _LEAD.match(path)
            if one:
                lead_id = one.group(1)
                if not db.delete_lead(lead_id):
                    return self._json({"error": "No such lead."}, 404)
                # Worth a real log line, not debug: this is the one destructive
                # route, so the terminal should show what left the table.
                log.info("deleted lead %s", lead_id)
                return self._json({"ok": True, "id": lead_id})

            self._json({"error": f"No route for {path}."}, 404)
        except BrokenPipeError:
            pass
        except Exception as e:
            log.exception("api delete failed: %s", path)
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)


def serve_forever(host: str | None = None, port: int | None = None) -> ThreadingHTTPServer:
    """Bind and serve on a daemon thread. Returns the server so callers can shut it down."""
    host = host or settings.api_host
    port = port if port is not None else settings.api_port
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, name="presence-api", daemon=True).start()
    log.info("api on http://%s:%d — GET /api/leads", host, port)
    return httpd
