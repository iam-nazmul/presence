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


def test_leads_without_an_email_are_not_deduplicated(fresh) -> None:
    """A phone-only lead is valid, and every one of them has email IS NULL."""
    a = db.save_lead("default", {"name": "Walk-in", "phone": "01755529304"})
    b = db.save_lead("default", {"name": "Walk-in", "phone": "01755529304"})
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
