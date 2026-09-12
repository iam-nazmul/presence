"""Cross-surface identity. Without this, Presence is five chatbots."""

from __future__ import annotations

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


def test_unknown_sender_still_gets_a_principal(fresh) -> None:
    pid, linked = db.principal_for("telegram", "999")
    assert pid != "owner" and linked is False


def test_link_makes_memory_visible_from_both_surfaces(fresh) -> None:
    db.remember("owner", "project", "Building Presence")
    tg, _ = db.principal_for("telegram", "12345", "Nazmul")
    assert not db.recall(tg, "project"), "a stranger must not read owner memory"

    code = db.mint_link_code("owner")
    winner = db.redeem_link_code(code, "telegram", "12345")

    assert winner == "owner"
    again, linked = db.principal_for("telegram", "12345")
    assert again == "owner" and linked
    assert db.recall("owner", "project"), "memory survived the merge"


def test_owner_wins_the_merge_in_either_direction(fresh) -> None:
    """Minting on the phone and redeeming in the terminal must not demote the terminal."""
    tg, _ = db.principal_for("telegram", "777", "Nazmul")
    db.remember(tg, "phone_fact", "learned on the phone")

    code = db.mint_link_code(tg)
    winner = db.redeem_link_code(code, "cli", "local")

    assert winner == "owner"
    cli_pid, _ = db.principal_for("cli", "local")
    tg_pid, _ = db.principal_for("telegram", "777")
    assert cli_pid == tg_pid == "owner"
    assert any(r["key"] == "phone_fact" for r in db.recall("owner"))


def test_a_used_code_cannot_be_replayed(fresh) -> None:
    code = db.mint_link_code("owner")
    assert db.redeem_link_code(code, "telegram", "1") == "owner"
    assert db.redeem_link_code(code, "telegram", "2") is None


def test_dedupe_stops_the_same_message_running_twice(fresh) -> None:
    assert db.already_seen("telegram", "chat:1") is False
    assert db.already_seen("telegram", "chat:1") is True
