"""Lead capture pack.

The point of the product: a stranger walks into a Telegram chat, talks, maybe
photographs an ID card, and a clean lead row lands in the system -- but only
after a human has read it back and pressed a button.

`business` is the tenant key. One deployment serves several business lines; the
agent passes whichever one the conversation is about and defaults to 'default'.
"""

from __future__ import annotations

from presence.core.envelope import Attachment
from presence.store import db
from presence.tools.registry import ToolContext, tool


def _clean(v: str | None) -> str | None:
    v = (v or "").strip()
    return v or None


@tool(
    risk="destructive",
    scopes={"lead"},
    confirm=("Save this lead?\n\nName: {name}\nPhone: {phone}\nEmail: {email}\n"
             "Company: {company}\nInterested in: {interest}\n\n"
             "Press Yes and I'll record it."),
)
async def save_lead(ctx: ToolContext, name: str, phone: str = "", email: str = "",
                    company: str = "", interest: str = "", note: str = "",
                    id_number: str = "", business: str = "default") -> str:
    """Save a new customer lead into the system.

    Call this only once you have at least a name and one way to reach them --
    a phone number or an email. The person is shown these exact details and has
    to approve them before anything is written, so put in what they actually
    said, never a guess or a placeholder.

    If they uploaded a photo of an ID or a card, read the details off it and
    pass them here; the photo itself is attached to the lead automatically.
    Use id_number only for a number actually printed on an uploaded document.

    One lead per email address. If that address is already on file this comes
    back refused, naming the existing reference -- so pass the email they
    actually gave you and never retype an address from an earlier lead.
    """
    fields = {
        "name": _clean(name), "phone": _clean(phone), "email": _clean(email),
        "company": _clean(company), "interest": _clean(interest),
        "note": _clean(note), "id_number": _clean(id_number),
    }
    if not fields["name"]:
        return "A lead needs a name. Ask them what to put down, then call save_lead again."
    if not (fields["phone"] or fields["email"]):
        return ("A lead needs a phone number or an email address. Ask for one, then "
                "call save_lead again.")

    env = ctx.envelope

    # Prefer a photo on this very message, but fall back to the last one parked
    # for the conversation. Normally it is the fallback that hits: this tool runs
    # after the confirmation button, and a button press carries no attachment.
    shot: Attachment | None = next(
        (a for a in getattr(env, "attachments", []) if a.kind == "image" and a.data), None
    )
    blob, mime = (shot.data, shot.mime or "image/jpeg") if shot else (None, None)
    if blob is None:
        found = db.latest_upload(ctx.conv_key, "image")
        if found:
            blob, mime = found

    try:
        lead_id = db.save_lead(
            _clean(business) or "default", fields,
            surface=env.surface, conv_key=ctx.conv_key, principal_id=ctx.principal_id,
            attachment=blob, attachment_kind=mime,
        )
    except db.DuplicateLead as dup:
        # An email is one person, so a second lead on it is almost always the
        # same conversation happening twice -- a retry, or someone who came
        # back a week later. Hand the model the row it collided with and tell
        # it what to say, because the alternative is a silent second row or a
        # bare "error" the person hears as a rejection.
        prior = dup.existing
        held = ", ".join(f"{f}={prior[f]}" for f in ("name", "phone", "company") if prior[f])
        return (f"Not saved -- there is already a lead on file for {fields['email']}: "
                f"reference {prior['id']}, captured {prior['created_at'][:10]}"
                f"{' (' + held + ')' if held else ''}. Do not call save_lead again for "
                f"this address. Tell them their details are already recorded, give them "
                f"that reference, and ask whether anything has changed -- if it has, say "
                f"someone will update the record. Only if they are a genuinely different "
                f"person sharing the address, ask for another email and try that.")

    shown = ", ".join(f"{k}={v}" for k, v in fields.items() if v)
    extra = " Their uploaded photo is attached to it." if blob else ""
    return (f"Saved lead {lead_id} for business '{_clean(business) or 'default'}' "
            f"({shown}).{extra} Tell them it is recorded and give them the reference "
            f"{lead_id}.")


@tool(risk="read", scopes={"lead"})
async def list_leads(ctx: ToolContext, business: str = "", limit: int = 10) -> str:
    """List the most recently captured leads, newest first.

    Leave business empty to see every business line. Use this when someone asks
    what has come in, or to check whether a lead was already recorded.
    """
    try:
        n = max(1, min(int(limit or 10), 50))
    except (TypeError, ValueError):
        n = 10

    rows = db.list_leads(_clean(business), n)
    if not rows:
        where = f" for business '{business}'" if _clean(business) else ""
        return f"No leads captured yet{where}."

    lines = []
    for r in rows:
        bits = [f"{f}={r[f]}" for f in db.LEAD_FIELDS if r[f]]
        mark = " [has photo]" if r["has_file"] else ""
        lines.append(f"{r['id']} · {r['business']} · {r['status']} · "
                     f"{r['created_at'][:16]}{mark}\n   {', '.join(bits)}")
    return f"{len(rows)} lead(s), newest first:\n" + "\n".join(lines)
