"""The tools that reach the machine, and the fence around them.

The policy tests here matter more than the rest of the file: this same agent
answers strangers on WhatsApp, and the only thing between one of them and a
shell is scopes_for().
"""

from __future__ import annotations

import asyncio

import pytest

from presence.core.capabilities import WHATSAPP
from presence.core.envelope import Conversation, Envelope, Identity
from presence.tools import registry, workspace


@pytest.fixture()
def ws(tmp_path, monkeypatch):
    from presence.config import settings

    monkeypatch.setattr(settings, "workspace_root", str(tmp_path))
    (tmp_path / "Desktop").mkdir()
    return tmp_path


def run(coro):
    return asyncio.run(coro)


def env(trust: str) -> Envelope:
    return Envelope(
        identity=Identity("whatsapp", "1"),
        conversation=Conversation("whatsapp", "c"),
        text="", capabilities=WHATSAPP, trust=trust,
    )


# --- the fence ------------------------------------------------------------


def test_relative_escapes_are_refused(ws) -> None:
    out = run(workspace.list_dir(None, "../../../etc"))
    assert "outside the workspace" in out


def test_absolute_paths_outside_the_root_are_refused(ws) -> None:
    out = run(workspace.read_file(None, "/etc/hosts"))
    assert "outside the workspace" in out


def test_a_symlink_cannot_be_used_to_step_out(ws) -> None:
    """resolve() runs before the containment check precisely for this."""
    (ws / "escape").symlink_to("/etc")
    out = run(workspace.list_dir(None, "escape"))
    assert "outside the workspace" in out


def test_credential_directories_are_refused_even_inside_the_root(ws) -> None:
    (ws / ".ssh").mkdir()
    (ws / ".ssh" / "id_rsa").write_text("PRIVATE KEY")
    out = run(workspace.read_file(None, ".ssh/id_rsa"))
    assert "off limits" in out
    assert "PRIVATE KEY" not in out


def test_work_inside_the_root_is_allowed(ws) -> None:
    assert "Wrote" in run(workspace.write_file(None, "Desktop/a.txt", "hi"))
    assert (ws / "Desktop" / "a.txt").read_text() == "hi"


# --- the tools ------------------------------------------------------------


def test_write_file_will_not_clobber_without_being_told(ws) -> None:
    run(workspace.write_file(None, "notes.txt", "original"))
    out = run(workspace.write_file(None, "notes.txt", "replacement"))
    assert "already exists" in out
    assert (ws / "notes.txt").read_text() == "original", "must not have been replaced"

    run(workspace.write_file(None, "notes.txt", "replacement", overwrite=True))
    assert (ws / "notes.txt").read_text() == "replacement"


def test_write_file_creates_missing_folders(ws) -> None:
    run(workspace.write_file(None, "Desktop/proj/src/main.py", "print(1)"))
    assert (ws / "Desktop" / "proj" / "src" / "main.py").exists()


def test_read_file_says_so_rather_than_dumping_binary(ws) -> None:
    (ws / "logo.png").write_bytes(b"\x89PNG\x00\x01\x02\xff\xfe")
    assert "binary" in run(workspace.read_file(None, "logo.png"))


def test_a_command_reports_its_output_and_where_it_ran(ws) -> None:
    out = run(workspace.run_command(None, "echo hello", cwd="Desktop"))
    assert "hello" in out and "succeeded" in out
    assert str(ws / "Desktop") in out


def test_a_failing_command_reports_the_exit_code(ws) -> None:
    """Swallowing this is how the agent ends up reporting success it never saw."""
    out = run(workspace.run_command(None, "exit 3"))
    assert "failed (exit 3)" in out


def test_a_hanging_command_is_killed_rather_than_held(ws) -> None:
    out = run(workspace.run_command(None, "sleep 30", timeout=1))
    assert "still running" in out


def test_a_command_cannot_run_outside_the_root(ws) -> None:
    out = run(workspace.run_command(None, "ls", cwd="/etc"))
    assert "outside the workspace" in out


# --- who is allowed to do any of this -------------------------------------


@pytest.mark.parametrize("trust", ["member", "guest"])
def test_a_stranger_is_never_even_told_these_tools_exist(trust) -> None:
    registry.load_packs()
    names = {s["function"]["name"] for s in registry.specs_for(trust)}
    assert not names & {"run_command", "write_file", "read_file", "list_dir", "which"}


@pytest.mark.parametrize("trust", ["member", "guest"])
@pytest.mark.parametrize("name", ["run_command", "write_file", "read_file"])
def test_a_stranger_calling_one_anyway_is_denied(trust, name) -> None:
    """specs_for hides them, but a model can still invent a name it once saw."""
    registry.load_packs()
    assert registry.decide(registry.TOOLS[name], env(trust)).kind == "deny"


def test_the_owner_must_confirm_every_command() -> None:
    """run_command is the one tool that never runs unwatched."""
    registry.load_packs()
    assert registry.decide(registry.TOOLS["run_command"], env("owner")).kind == "confirm"


def test_the_owner_can_look_around_without_a_button() -> None:
    registry.load_packs()
    for name in ("list_dir", "read_file", "which", "write_file"):
        assert registry.decide(registry.TOOLS[name], env("owner")).kind == "allow", name
