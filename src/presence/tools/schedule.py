"""Scheduling pack. The agent's ability to come back to you unprompted.

A trigger becomes a synthetic Envelope on the same queue as a real message, so
proactive work runs through the identical code path. There is no separate
"proactive" branch anywhere in the harness.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from presence.store import db
from presence.tools.registry import ToolContext, tool

_UNITS = {
    "sec": 1, "second": 1, "seconds": 1, "s": 1,
    "min": 60, "minute": 60, "minutes": 60, "m": 60,
    "hour": 3600, "hours": 3600, "h": 3600,
    "day": 86400, "days": 86400, "d": 86400,
    "week": 604800, "weeks": 604800,
}


def parse_when(when: str) -> datetime | None:
    """Accepts 'in 5 minutes', '90s', 'tomorrow 09:00', '15:30', or ISO-8601."""
    s = (when or "").strip().lower()
    now = datetime.now(UTC)
    if not s:
        return None

    m = re.match(r"^(?:in\s+)?(\d+(?:\.\d+)?)\s*([a-z]+)$", s)
    if m and m.group(2) in _UNITS:
        return now + timedelta(seconds=float(m.group(1)) * _UNITS[m.group(2)])

    m = re.match(r"^(today|tomorrow)?\s*(?:at\s+)?(\d{1,2}):(\d{2})$", s)
    if m:
        day = now + timedelta(days=1 if m.group(1) == "tomorrow" else 0)
        target = day.replace(hour=int(m.group(2)), minute=int(m.group(3)),
                             second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return target

    try:
        dt = datetime.fromisoformat(when.strip())
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except ValueError:
        return None


@tool(risk="write", scopes={"schedule"})
async def schedule(ctx: ToolContext, when: str, what: str) -> str:
    """Schedule yourself to do something later and message the person about it,
    without them asking again.

    'when' accepts "in 10 minutes", "in 2 hours", "tomorrow 09:00" or an ISO
    timestamp. 'what' is the instruction you will be given at that time -- write
    it as a task for your future self, e.g. "check the HN front page for posts
    about agent harnesses and send the two most relevant".

    You will be woken up on whichever surface this person was last active on.
    """
    at = parse_when(when)
    if at is None:
        return (f"I could not read '{when}' as a time. Try 'in 20 minutes', "
                f"'tomorrow 09:00', or an ISO timestamp.")
    if not (what or "").strip():
        return "Nothing scheduled: say what should happen."

    target = db.preferred_conversation(ctx.principal_id) or ctx.conv_key
    tid = db.add_trigger(ctx.principal_id, what.strip(), at.isoformat(), target)
    local = at.astimezone()
    return (f"Scheduled ({tid}) for {local:%a %H:%M}. I will pick this up on "
            f"{target.split(':')[0]} and message you then.")


@tool(risk="read", scopes={"schedule"})
async def list_schedules(ctx: ToolContext) -> str:
    """List everything you are scheduled to do for this person."""
    rows = db.list_triggers(ctx.principal_id)
    if not rows:
        return "Nothing scheduled."
    out = []
    for r in rows[:20]:
        at = datetime.fromisoformat(r["run_at"]).astimezone()
        out.append(f"- {r['id']} at {at:%a %d %b %H:%M}: {r['prompt'][:100]}")
    extra = f"\n…{len(rows) - 20} more." if len(rows) > 20 else ""
    return "Scheduled:\n" + "\n".join(out) + extra


@tool(risk="write", scopes={"schedule"}, confirm="Cancel {trigger_id}?")
async def cancel_schedule(ctx: ToolContext, trigger_id: str) -> str:
    """Cancel a scheduled item by its id. Use list_schedules to find the id."""
    ok = db.cancel_trigger(ctx.principal_id, trigger_id.strip())
    return f"Cancelled {trigger_id}." if ok else f"No active schedule called '{trigger_id}'."
