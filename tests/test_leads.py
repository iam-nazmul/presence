"""One lead per email address.

The table filled up with the same person eleven times over during a demo, so
the constraint is the feature: a second capture on a known address has to be
refused, and refused in words the agent can read back to the person.
"""

from __future__ import annotations

import asyncio
import sqlite3

import pytest

from presence.store import db


@pytest.fixture()
def fresh(tmp_path, monkeypatch):
    from presence.config import settings

    monkeypatch.setattr(settings, "db_path", str(tmp_path / "t.db"))
    db._local.__dict__.pop("conn", None)
    db.init()
    yield db
    db._local.__dict__.pop("conn", None)


def test_second_lead_on_the_same_email_is_refused(fresh) -> None:
    first = db.save_lead("default", {"name": "Nusrat", "email": "nusrat@glascutr.com"})
    with pytest.raises(db.DuplicateLead) as caught:
        db.save_lead("default", {"name": "Nusrat Azim", "email": "nusrat@glascutr.com"})
    assert caught.value.existing["id"] == first, "the error has to name the lead on file"
    assert db.count_leads() == 1


def test_duplicate_email_ignores_case_and_padding(fresh) -> None:
    """Addresses are typed by strangers and read off photographs."""
    db.save_lead("default", {"name": "Sabbir", "email": "sabbir@quantanite.com"})
    for variant in ("Sabbir@Quantanite.com", "  sabbir@quantanite.com  ",
                    "SABBIR@QUANTANITE.COM"):
        with pytest.raises(db.DuplicateLead):
            db.save_lead("default", {"name": "Sabbir", "email": variant})
    assert db.count_leads() == 1


def test_uniqueness_holds_across_business_lines(fresh) -> None:
    """`business` is whatever the model passed, so it cannot be part of the key."""
    db.save_lead("default", {"name": "Belal", "email": "belal@bs23.com"})
    with pytest.raises(db.DuplicateLead):
        db.save_lead("Senior Software Engineer I", {"name": "Belal", "email": "belal@bs23.com"})


def test_leads_without_an_email_still_save_alongside_each_other(fresh) -> None:
    """A phone-only lead is valid, and every one of them has email IS NULL --
    which a unique index does not treat as a collision. Different people, so
    the phone check below does not catch them either."""
    a = db.save_lead("default", {"name": "Walk-in", "phone": "01755529304"})
    b = db.save_lead("default", {"name": "Walk-in", "phone": "01755529999"})
    c = db.save_lead("default", {"name": "Blank", "email": ""})
    assert len({a, b, c}) == 3
    assert db.count_leads() == 3


def test_the_index_is_actually_in_the_database(fresh) -> None:
    """Not just the Python check -- a second writer has to bounce off SQLite."""
    rows = db.conn().execute(
        "SELECT name FROM sqlite_master WHERE type = 'index' AND name = ?",
        ("idx_leads_email_unique",),
    ).fetchall()
    assert rows, "the unique index was not created"
    db.save_lead("default", {"name": "A", "email": "a@example.com"})
    with pytest.raises(sqlite3.IntegrityError):
        db.conn().execute(
            "INSERT INTO leads (id, business, email, status, created_at)"
            " VALUES ('RAW1', 'default', 'A@Example.com ', 'new', ?)", (db.now(),)
        )


def test_existing_duplicates_downgrade_to_a_warning(tmp_path, monkeypatch, caplog) -> None:
    """A database captured before this change must still open.

    The index lives in Python, not schema.sql, precisely so this case logs
    instead of raising out of executescript on every single connection.
    """
    from presence.config import settings

    path = tmp_path / "dupes.db"
    monkeypatch.setattr(settings, "db_path", str(path))
    db._local.__dict__.pop("conn", None)
    db.init()
    # Plant the collision behind the index's back, then force a fresh connection.
    db.conn().execute("DROP INDEX idx_leads_email_unique")
    for i in (1, 2):
        db.conn().execute(
            "INSERT INTO leads (id, business, email, status, created_at)"
            " VALUES (?, 'default', 'twice@example.com', 'new', ?)", (f"DUP{i}", db.now())
        )
    db.conn().commit()
    db._local.__dict__.pop("conn", None)

    with caplog.at_level("WARNING"):
        assert db.count_leads() == 2, "the database still has to open"
    assert "twice@example.com" in caplog.text
    db._local.__dict__.pop("conn", None)


def test_tool_returns_a_sentence_naming_the_existing_reference(fresh) -> None:
    """The tool result is what the model reads back, so it must be usable prose."""
    from presence.core.capabilities import CLI
    from presence.core.envelope import Conversation, Envelope, Identity
    from presence.tools.leads import save_lead
    from presence.tools.registry import ToolContext

    existing = db.save_lead("default", {"name": "Nusrat Azim", "phone": "01755529304",
                                        "email": "nusrat@glascutr.com"})

    env = Envelope(
        identity=Identity("cli", "local", "tester"),
        conversation=Conversation("cli", "test", None, True),
        text="",
        capabilities=CLI,
        trust="owner",
        principal_id="owner",
    )
    ctx = ToolContext("owner", env, "run_test", "cli:test:-")
    out = asyncio.run(save_lead(ctx, name="Nusrat", email="NUSRAT@glascutr.com"))

    assert existing in out, "the model needs the reference to give the person"
    assert "Not saved" in out
    assert db.count_leads() == 1


# --- a business card, photographed --------------------------------------


def card_envelope(*, with_photo: bool):
    """A WhatsApp message with a snapshot of a business card on it."""
    from presence.core.capabilities import WHATSAPP
    from presence.core.envelope import Attachment, Conversation, Envelope, Identity

    shot = [Attachment(kind="image", name="photo", mime="image/jpeg", data=b"\xff\xd8card")]
    return Envelope(
        identity=Identity("whatsapp", "8801700000000", "Rina"),
        conversation=Conversation("whatsapp", "8801700000000@s.whatsapp.net", None, True),
        text="",
        capabilities=WHATSAPP,
        trust="guest",
        attachments=shot if with_photo else [],
    )


def test_the_card_itself_reaches_the_model(fresh) -> None:
    """A photo with no caption is still a message. It has to arrive as image
    parts, or the agent is reading a blank turn and asks them to type it out."""
    from presence.agent.prompts import build_messages

    env = card_envelope(with_photo=True)
    content = build_messages(env, env.conversation.key, "p1")[-1]["content"]

    assert isinstance(content, list), "a photo turn has to be multimodal content"
    assert any(p["type"] == "image_url" for p in content)
    assert "[attached: photo]" in content[0]["text"]


def test_a_photo_turn_is_routed_to_the_vision_model(monkeypatch) -> None:
    """Most local chat models cannot see, and they do not say so -- the picture
    is dropped and the answer comes back as though no card was sent."""
    from presence.config import settings
    from presence.providers.openai_compat import ModelRouter

    monkeypatch.setattr(settings, "model_main", "qwen3.5:9b")
    monkeypatch.setattr(settings, "model_vision", "qwen2.5vl:7b")

    plain = [{"role": "user", "content": "hi"}]
    withphoto = [{"role": "user", "content": [
        {"type": "text", "text": "hi"},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}},
    ]}]

    assert ModelRouter.for_messages(plain) == "qwen3.5:9b"
    assert ModelRouter.for_messages(withphoto) == "qwen2.5vl:7b"

    # Unset, the main model keeps both -- right for a hosted multimodal model.
    monkeypatch.setattr(settings, "model_vision", "")
    assert ModelRouter.for_messages(withphoto) == "qwen3.5:9b"


def test_the_card_is_still_attached_when_the_lead_is_written(fresh) -> None:
    """save_lead runs after the confirmation, and a "yes" carries no photo. The
    card has to come back out of uploads or the lead lands without it."""
    from presence.tools.leads import save_lead
    from presence.tools.registry import ToolContext

    env = card_envelope(with_photo=True)
    conv_key = env.conversation.key
    db.save_upload(conv_key, "image", "image/jpeg", b"\xff\xd8card")

    # The confirmation turn: same conversation, no attachment on the message.
    ctx = ToolContext("p1", card_envelope(with_photo=False), "run1", conv_key)
    out = asyncio.run(save_lead(ctx, name="Rina Haque", phone="+8801700000000",
                                email="rina@glascutr.com", company="Glascutr",
                                interest="glass fitting"))

    assert "Saved lead" in out and "photo is attached" in out
    lead_id = out.split("Saved lead ")[1].split()[0]
    assert db.lead_photo(lead_id) == (b"\xff\xd8card", "image/jpeg")


def test_a_card_older_than_the_window_is_not_attached_to_someone_elses_lead(fresh) -> None:
    """latest_upload is time-bounded on purpose: a photo from an hour-old
    conversation must not end up filed against a stranger."""
    from presence.tools.leads import save_lead
    from presence.tools.registry import ToolContext

    env = card_envelope(with_photo=False)
    ctx = ToolContext("p1", env, "run1", env.conversation.key)
    out = asyncio.run(save_lead(ctx, name="No Card", phone="+8801711111111"))

    assert "Saved lead" in out and "photo is attached" not in out


def test_a_photo_that_never_downloaded_is_not_reported_as_seen(fresh) -> None:
    """The prompt tells the model it can always read what was sent. A 6MB card
    photo that hit the size cap is the one case where that is not true, and
    describing a card it was never shown is the worst possible failure here."""
    from presence.agent.prompts import said
    from presence.core.envelope import Attachment

    env = card_envelope(with_photo=False)
    env.attachments.append(Attachment(kind="image", name="photo", mime="image/jpeg",
                                      problem="the photo would not download"))
    assert "would not download" in said(env)


# --- the same card, photographed twice ------------------------------------


def test_the_same_card_twice_does_not_make_two_leads(fresh) -> None:
    """The failure this prevents is mundane: a card photographed, the photo not
    obviously landing, and the same card sent again a minute later."""
    first = db.save_lead("default", {"name": "Rina Haque", "phone": "+880 1700-000000"})
    with pytest.raises(db.DuplicateLead) as caught:
        db.save_lead("default", {"name": "Rina Haque", "phone": "01700000000"})
    assert caught.value.existing["id"] == first
    assert caught.value.field == "phone"
    assert db.count_leads() == 1


def test_a_number_matches_however_it_was_written_down() -> None:
    """Printed on a card, typed by the person, entered by a colleague."""
    same = {"+880 1700-000000", "01700000000", "8801700000000", "+8801700 000 000"}
    assert len({db.phone_key(n) for n in same}) == 1
    assert db.phone_key("01700000001") != db.phone_key("01700000000")
    assert db.phone_key("") is None


def test_two_colleagues_on_one_switchboard_are_two_leads(fresh) -> None:
    """Both cards carry the same office number, and they are not the same
    person. An email settles it, so the number is only consulted without one."""
    db.save_lead("default", {"name": "Rina", "email": "rina@glascutr.com",
                             "phone": "+880 2 55667788"})
    second = db.save_lead("default", {"name": "Imran", "email": "imran@glascutr.com",
                                      "phone": "+880 2 55667788"})
    assert second
    assert db.count_leads() == 2


def test_the_refusal_names_the_field_that_matched(fresh) -> None:
    """The model has to say something true back: "we already have this number"
    is a different sentence from "we already have this address"."""
    import asyncio

    from presence.tools.leads import save_lead
    from presence.tools.registry import ToolContext

    env = card_envelope(with_photo=False)
    ctx = ToolContext("p1", env, "run1", env.conversation.key)
    asyncio.run(save_lead(ctx, name="Rina Haque", phone="+8801700000000"))
    again = asyncio.run(save_lead(ctx, name="Rina Haque", phone="01700000000"))

    assert "Not saved" in again
    assert "phone" in again and "already" in again
    assert db.count_leads() == 1
