"""SQLite store. One file, WAL, no ORM.

Everything the harness remembers lives here: who people are across surfaces,
what was said, what the agent knows, and an audit row for every run and every
tool call.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from presence.config import settings

log = logging.getLogger("presence.store")

_SCHEMA = Path(__file__).parent / "schema.sql"

# One connection per thread. A sqlite3.Connection is not safe to use from two
# threads at once, and several Presence processes share the file besides, so
# isolation here plus WAL and a busy_timeout below covers both cases.
_local = threading.local()


def now() -> str:
    return datetime.now(UTC).isoformat()


def _uid() -> str:
    return uuid.uuid4().hex


def conn() -> sqlite3.Connection:
    """One connection per process, in autocommit, with WAL and a real busy wait.

    Several Presence processes share this file during a demo -- a terminal, the
    Telegram surface, the scheduler. Without WAL plus a busy_timeout, the second
    writer fails instantly with "database is locked" instead of waiting its turn.
    """
    c = getattr(_local, "conn", None)
    if c is None:
        c = sqlite3.connect(settings.db_path, timeout=15.0, isolation_level=None)
        c.row_factory = sqlite3.Row
        # PRAGMAs must run outside a transaction, so before the schema script.
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=15000")
        c.execute("PRAGMA synchronous=NORMAL")
        # lead_by_phone compares numbers that were written down three different
        # ways, and sqlite has no way to do that without help.
        c.create_function("phonekey", 1, phone_key, deterministic=True)
        c.executescript(_SCHEMA.read_text())
        _ensure_unique_lead_email(c)
        _local.conn = c
    return c


# One lead per email address. Deliberately *not* in schema.sql: that file runs
# through executescript on every new connection, so a database that already
# holds two rows with the same email would raise here and take the whole
# process down rather than just refusing the next duplicate. Creating it in
# Python lets a collision degrade into a warning that names the problem.
#
# lower(trim(...)) because these addresses are typed by strangers or read off a
# photograph -- "Nazmul@Gmail.com " and "nazmul@gmail.com" are one person, and
# an index on the raw column would happily store both. Partial, because a
# phone-only lead is valid and every one of those has email IS NULL.
_UNIQUE_EMAIL = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_leads_email_unique
  ON leads (lower(trim(email)))
  WHERE email IS NOT NULL AND trim(email) <> ''
"""


def _ensure_unique_lead_email(c: sqlite3.Connection) -> None:
    try:
        c.execute(_UNIQUE_EMAIL)
    except sqlite3.IntegrityError:
        dupes = c.execute(
            "SELECT lower(trim(email)) AS email, COUNT(*) AS n FROM leads"
            " WHERE email IS NOT NULL AND trim(email) <> ''"
            " GROUP BY lower(trim(email)) HAVING n > 1 ORDER BY n DESC"
        ).fetchall()
        log.warning(
            "leads table still holds %d duplicated email address(es), so the unique "
            "index was not created and duplicates are not being refused yet: %s. "
            "Delete the extra rows in the portal and restart.",
            len(dupes), ", ".join(f"{r['email']} x{r['n']}" for r in dupes[:5]),
        )


def init() -> None:
    """Create the schema and seed the owner principal from OWNER_IDENTITIES."""
    c = conn()
    owner = c.execute("SELECT id FROM principals WHERE id = 'owner'").fetchone()
    if owner is None:
        c.execute(
            "INSERT INTO principals (id, display_name, tz, locale, created_at) VALUES (?,?,?,?,?)",
            ("owner", settings.owner_name, "Asia/Dhaka", "en", now()),
        )
    for spec in settings.owner_identities:
        if ":" not in spec:
            continue
        surface, external_id = spec.split(":", 1)
        c.execute(
            "INSERT OR IGNORE INTO identities (surface, external_id, principal_id,"
            " display_name, linked_at) VALUES (?,?,?,?,?)",
            (surface.strip(), external_id.strip(), "owner", settings.owner_name, now()),
        )
    c.commit()


# --- identity -------------------------------------------------------------


def principal_for(surface: str, external_id: str,
                  display_name: str | None = None) -> tuple[str, bool]:
    """Resolve an identity to a principal. Returns (principal_id, is_linked).

    An unknown sender still gets a principal so the conversation works -- it is
    simply not linked to the owner, which is what drives the trust level.
    """
    c = conn()
    row = c.execute(
        "SELECT principal_id FROM identities WHERE surface = ? AND external_id = ?",
        (surface, external_id),
    ).fetchone()
    if row:
        return row["principal_id"], True

    pid = f"p_{_uid()[:12]}"
    c.execute(
        "INSERT INTO principals (id, display_name, tz, locale, created_at) VALUES (?,?,?,?,?)",
        (pid, display_name, None, None, now()),
    )
    c.execute(
        "INSERT INTO identities (surface, external_id, principal_id, display_name, linked_at)"
        " VALUES (?,?,?,?,?)",
        (surface, external_id, pid, display_name, now()),
    )
    c.commit()
    return pid, False


def mint_link_code(principal_id: str, ttl_minutes: int = 10) -> str:
    import random
    import string

    code = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    expires = (datetime.now(UTC) + timedelta(minutes=ttl_minutes)).isoformat()
    c = conn()
    c.execute(
        "INSERT INTO link_codes (code, principal_id, created_at, expires_at) VALUES (?,?,?,?)",
        (code, principal_id, now(), expires),
    )
    c.commit()
    return code


def redeem_link_code(code: str, surface: str, external_id: str) -> str | None:
    """Merge this identity into the code's principal. Returns the principal id."""
    c = conn()
    row = c.execute(
        "SELECT principal_id, expires_at, used_at FROM link_codes WHERE code = ?",
        (code.upper().strip(),),
    ).fetchone()
    if row is None or row["used_at"] or row["expires_at"] < now():
        return None

    minted_by = row["principal_id"]
    old = c.execute(
        "SELECT principal_id FROM identities WHERE surface = ? AND external_id = ?",
        (surface, external_id),
    ).fetchone()
    redeemed_by = old["principal_id"] if old else None

    # The owner principal always wins a merge, whichever side minted the code.
    # Otherwise redeeming in the terminal would silently demote the terminal.
    winner = "owner" if "owner" in (minted_by, redeemed_by) else minted_by
    loser = redeemed_by if winner == minted_by else minted_by

    c.execute(
        "INSERT INTO identities (surface, external_id, principal_id, display_name, linked_at)"
        " VALUES (?,?,?,NULL,?)"
        " ON CONFLICT(surface, external_id) DO UPDATE SET principal_id = excluded.principal_id,"
        " linked_at = excluded.linked_at",
        (surface, external_id, winner, now()),
    )
    if loser and loser != winner:
        # carry everything the losing principal owned across, identities included
        for table in ("memories", "conversations", "identities", "triggers"):
            c.execute(f"UPDATE {table} SET principal_id = ? WHERE principal_id = ?",
                      (winner, loser))
        c.execute("DELETE FROM principals WHERE id = ?", (loser,))
    c.execute("UPDATE link_codes SET used_at = ? WHERE code = ?", (now(), code.upper().strip()))
    c.commit()
    return winner


def identities_of(principal_id: str) -> list[sqlite3.Row]:
    return conn().execute(
        "SELECT * FROM identities WHERE principal_id = ? ORDER BY linked_at", (principal_id,)
    ).fetchall()


# --- dedupe ---------------------------------------------------------------


def already_seen(surface: str, external_id: str | None) -> bool:
    """True when this exact surface message has been processed before.

    Slack and WhatsApp both retry webhooks. Without this the agent answers
    every message twice.
    """
    if not external_id:
        return False
    c = conn()
    try:
        c.execute("INSERT INTO seen (surface, external_id, seen_at) VALUES (?,?,?)",
                  (surface, external_id, now()))
        c.commit()
        return False
    except sqlite3.IntegrityError:
        return True


# --- conversations and messages -------------------------------------------


def touch_conversation(conv_key: str, surface: str, channel_id: str,
                       thread_id: str | None, principal_id: str) -> None:
    c = conn()
    c.execute(
        "INSERT INTO conversations (key, surface, channel_id, thread_id, principal_id,"
        " title, created_at, last_seen_at) VALUES (?,?,?,?,?,NULL,?,?)"
        " ON CONFLICT(key) DO UPDATE SET last_seen_at = excluded.last_seen_at,"
        " principal_id = excluded.principal_id",
        (conv_key, surface, channel_id, thread_id, principal_id, now(), now()),
    )
    c.commit()


def add_message(conv_key: str, role: str, content: Any) -> None:
    conn().execute(
        "INSERT INTO messages (id, conv_key, role, content_json, created_at) VALUES (?,?,?,?,?)",
        (_uid(), conv_key, role, json.dumps(content), now()),
    )
    conn().commit()


def history(conv_key: str, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn().execute(
        "SELECT role, content_json FROM messages WHERE conv_key = ?"
        " ORDER BY created_at DESC, rowid DESC LIMIT ?",
        (conv_key, limit),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in reversed(rows):
        content = json.loads(r["content_json"])
        out.append(content if isinstance(content, dict)
                   else {"role": r["role"], "content": content})
    return out


def preferred_conversation(principal_id: str, exclude: str | None = None) -> str | None:
    """Where to reach this person when nobody asked. Most recent direct chat."""
    q = ("SELECT key FROM conversations WHERE principal_id = ? AND surface != 'system'"
         " AND surface != 'cli'")
    args: list[Any] = [principal_id]
    if settings.preferred_surface:
        q += " AND surface = ?"
        args.append(settings.preferred_surface)
    if exclude:
        q += " AND key != ?"
        args.append(exclude)
    q += " ORDER BY last_seen_at DESC LIMIT 1"
    row = conn().execute(q, args).fetchone()
    return row["key"] if row else None


# --- memory ---------------------------------------------------------------


def remember(principal_id: str, key: str, value: str, scope: str = "principal",
             scope_key: str | None = None, envelope_id: str | None = None) -> None:
    conn().execute(
        "INSERT INTO memories (id, principal_id, scope, scope_key, key, value,"
        " source_envelope_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(principal_id, scope, IFNULL(scope_key, ''), key)"
        " DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (_uid(), principal_id, scope, scope_key, key, value, envelope_id, now(), now()),
    )
    conn().commit()


def recall(principal_id: str, query: str | None = None, conv_key: str | None = None,
           limit: int = 10) -> list[sqlite3.Row]:
    sql = ("SELECT key, value, scope, updated_at FROM memories WHERE principal_id = ?"
           " AND (scope != 'conversation' OR scope_key = ?)")
    args: list[Any] = [principal_id, conv_key]
    if query:
        sql += " AND (key LIKE ? OR value LIKE ?)"
        args += [f"%{query}%", f"%{query}%"]
    sql += " ORDER BY updated_at DESC LIMIT ?"
    args.append(limit)
    return conn().execute(sql, args).fetchall()


def forget(principal_id: str, key: str) -> int:
    cur = conn().execute("DELETE FROM memories WHERE principal_id = ? AND key = ?",
                         (principal_id, key))
    conn().commit()
    return cur.rowcount


# --- audit ----------------------------------------------------------------


def start_run(envelope_id: str, conv_key: str, principal_id: str, surface: str,
              provider: str, model: str) -> str:
    rid = f"r_{_uid()[:12]}"
    conn().execute(
        "INSERT INTO runs (id, envelope_id, conv_key, principal_id, surface, provider,"
        " model, status, started_at) VALUES (?,?,?,?,?,?,?,'running',?)",
        (rid, envelope_id, conv_key, principal_id, surface, provider, model, now()),
    )
    conn().commit()
    return rid


def end_run(run_id: str, status: str, turns: int, error: str | None = None,
            pending: dict[str, Any] | None = None) -> None:
    conn().execute(
        "UPDATE runs SET status = ?, turns = ?, error = ?, pending_json = ?, ended_at = ?"
        " WHERE id = ?",
        (status, turns, error, json.dumps(pending) if pending else None, now(), run_id),
    )
    conn().commit()


def log_tool(run_id: str, name: str, args: dict[str, Any], decision: str,
             ok: bool, preview: str, ms: int) -> None:
    conn().execute(
        "INSERT INTO tool_calls (id, run_id, name, args_json, decision, ok, preview, ms,"
        " created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (_uid(), run_id, name, json.dumps(args)[:2000], decision, int(ok),
         preview[:400], ms, now()),
    )
    conn().commit()


def parked_run(conv_key: str) -> sqlite3.Row | None:
    return conn().execute(
        "SELECT * FROM runs WHERE conv_key = ? AND status = 'parked'"
        " ORDER BY started_at DESC LIMIT 1",
        (conv_key,),
    ).fetchone()


# --- triggers -------------------------------------------------------------


def add_trigger(principal_id: str, prompt: str, run_at: str, target_conv_key: str | None,
                kind: str = "once", cron: str | None = None) -> str:
    tid = f"t_{_uid()[:8]}"
    conn().execute(
        "INSERT INTO triggers (id, principal_id, kind, cron, run_at, prompt,"
        " target_conv_key, enabled, created_at) VALUES (?,?,?,?,?,?,?,1,?)",
        (tid, principal_id, kind, cron, run_at, prompt, target_conv_key, now()),
    )
    conn().commit()
    return tid


def due_triggers() -> list[sqlite3.Row]:
    return conn().execute(
        "SELECT * FROM triggers WHERE enabled = 1 AND run_at IS NOT NULL AND run_at <= ?",
        (now(),),
    ).fetchall()


def list_triggers(principal_id: str) -> list[sqlite3.Row]:
    return conn().execute(
        "SELECT * FROM triggers WHERE principal_id = ? AND enabled = 1 ORDER BY run_at",
        (principal_id,),
    ).fetchall()


def fire_trigger(trigger_id: str, next_run_at: str | None = None) -> None:
    if next_run_at:
        conn().execute("UPDATE triggers SET last_fired_at = ?, run_at = ? WHERE id = ?",
                       (now(), next_run_at, trigger_id))
    else:
        conn().execute("UPDATE triggers SET last_fired_at = ?, enabled = 0 WHERE id = ?",
                       (now(), trigger_id))
    conn().commit()


def cancel_trigger(principal_id: str, trigger_id: str) -> bool:
    cur = conn().execute("UPDATE triggers SET enabled = 0 WHERE id = ? AND principal_id = ?",
                         (trigger_id, principal_id))
    conn().commit()
    return cur.rowcount > 0


# --- leads -----------------------------------------------------------------

LEAD_FIELDS = ("name", "phone", "email", "company", "interest", "note", "id_number")


class DuplicateLead(Exception):
    """This person is already on file.

    Carries the existing row, and which field matched, so the caller can tell
    them *which* lead they already have rather than just refusing.
    """

    def __init__(self, existing: sqlite3.Row, field: str = "email",
                 value: str | None = None) -> None:
        super().__init__(f"lead {existing['id']} already uses that {field}")
        self.existing = existing
        self.field = field
        self.value = value or existing[field]


def phone_key(value: str | None) -> str | None:
    """The comparable part of a phone number.

    A card is printed "+880 1700-000000", the same person types "01700000000",
    and a colleague enters "8801700000000". The last nine digits are the only
    part that survives all three: the country code comes and goes, and the
    national trunk zero with it.
    """
    digits = re.sub(r"\D", "", value or "")
    if not digits:
        return None
    return digits[-9:] if len(digits) >= 9 else digits


def lead_by_phone(phone: str | None) -> sqlite3.Row | None:
    """The lead on file for this number, however either was written down.

    A scan, because the stored number keeps whatever punctuation it arrived
    with and there is no normalised column to index. Leads are counted in
    hundreds here; when that stops being true this wants a phone_key column.
    """
    key = phone_key(phone)
    if not key:
        return None
    return conn().execute(
        f"SELECT id, business, {', '.join(LEAD_FIELDS)}, status, created_at"
        " FROM leads WHERE phone IS NOT NULL AND phonekey(phone) = ? LIMIT 1",
        (key,),
    ).fetchone()


def lead_by_email(email: str | None) -> sqlite3.Row | None:
    """The lead on file for this address, matched the way the index matches."""
    email = (email or "").strip()
    if not email:
        return None
    return conn().execute(
        f"SELECT id, business, {', '.join(LEAD_FIELDS)}, status, created_at"
        " FROM leads WHERE lower(trim(email)) = lower(?) LIMIT 1",
        (email,),
    ).fetchone()


def save_lead(business: str, fields: dict[str, Any], *, surface: str | None = None,
              conv_key: str | None = None, principal_id: str | None = None,
              attachment: bytes | None = None,
              attachment_kind: str | None = None) -> str:
    """Insert one lead and return its id. Unknown keys in `fields` are dropped.

    Raises DuplicateLead when this person is already on file. Checked up front
    so the caller gets the existing row to talk about, and again off the
    IntegrityError because several Presence processes share this file and two
    surfaces can reach this line at once.

    An email decides it when there is one: two colleagues photographed at the
    same stand share the company switchboard on their cards, and refusing the
    second of them would be worse than a duplicate. With no email to go on, the
    number is what is left, and that is the case a re-photographed card hits.
    """
    row = {k: (fields.get(k) or None) for k in LEAD_FIELDS}

    existing = lead_by_email(row["email"])
    if existing is not None:
        raise DuplicateLead(existing, "email", row["email"])
    if not row["email"]:
        existing = lead_by_phone(row["phone"])
        if existing is not None:
            raise DuplicateLead(existing, "phone", row["phone"])

    lead_id = _uid()[:12].upper()
    try:
        conn().execute(
            f"""INSERT INTO leads (id, business, {', '.join(LEAD_FIELDS)}, source_surface,
                                   source_conv_key, principal_id, attachment_kind,
                                   attachment, status, created_at)
                VALUES (?, ?, {', '.join('?' * len(LEAD_FIELDS))}, ?, ?, ?, ?, ?, 'new', ?)""",
            (lead_id, business, *[row[k] for k in LEAD_FIELDS], surface, conv_key,
             principal_id, attachment_kind, attachment, now()),
        )
    except sqlite3.IntegrityError:
        # Lost the race, or the index caught a case the check above missed.
        won = lead_by_email(row["email"])
        if won is not None:
            raise DuplicateLead(won, "email", row["email"]) from None
        raise
    conn().commit()
    return lead_id


def save_upload(conv_key: str, kind: str, mime: str | None, data: bytes) -> str:
    """Hold an inbound file against its conversation so a later turn can claim it."""
    uid = _uid()
    conn().execute(
        "INSERT INTO uploads (id, conv_key, kind, mime, data, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (uid, conv_key, kind, mime, data, now()),
    )
    conn().commit()
    return uid


def latest_upload(conv_key: str, kind: str = "image",
                  within_minutes: int = 30) -> tuple[bytes, str] | None:
    """The most recent file of this kind in the conversation, as (data, mime).

    Time-bounded so a lead never silently picks up a photo from an unrelated
    conversation an hour earlier.
    """
    cutoff = (datetime.now(UTC) - timedelta(minutes=within_minutes)).isoformat()
    row = conn().execute(
        "SELECT data, mime FROM uploads WHERE conv_key = ? AND kind = ? AND created_at >= ? "
        "ORDER BY created_at DESC LIMIT 1",
        (conv_key, kind, cutoff),
    ).fetchone()
    if row is None:
        return None
    return row["data"], (row["mime"] or "image/jpeg")


def lead_photo(lead_id: str) -> tuple[bytes, str] | None:
    """The uploaded image for one lead, or None when it has no attachment.

    Separate from list_leads on purpose: that one selects `attachment IS NOT
    NULL` rather than the BLOB, so listing a hundred leads does not drag a
    hundred photos through memory.
    """
    row = conn().execute(
        "SELECT attachment, attachment_kind FROM leads WHERE id = ?", (lead_id,)
    ).fetchone()
    if row is None or row["attachment"] is None:
        return None
    return row["attachment"], (row["attachment_kind"] or "image")


def delete_lead(lead_id: str) -> bool:
    """Remove one lead. False when no such row existed.

    The portal is the only caller: a lead captured on stage by mistake has to
    be removable by the person standing there, and the alternative is editing
    the database by hand mid-demo.
    """
    cur = conn().execute("DELETE FROM leads WHERE id = ?", (lead_id,))
    conn().commit()
    return cur.rowcount > 0


def count_leads() -> int:
    return conn().execute("SELECT COUNT(*) AS c FROM leads").fetchone()["c"]


def list_leads(business: str | None = None, limit: int = 20) -> list[sqlite3.Row]:
    sql = (f"SELECT id, business, {', '.join(LEAD_FIELDS)}, source_surface, status, "
           "created_at, attachment IS NOT NULL AS has_file FROM leads")
    args: list[Any] = []
    if business:
        sql += " WHERE business = ?"
        args.append(business)
    sql += " ORDER BY created_at DESC LIMIT ?"
    args.append(limit)
    return conn().execute(sql, args).fetchall()
