"""Workspace pack -- the tools that let the agent actually do the work.

Without these it can describe how to start a Django project but never start
one, which is the difference between an assistant and a chatbot.

Two rules hold the whole thing together:

  * Everything is confined to WORKSPACE_ROOT. Paths are resolved (so symlinks
    and ../.. cannot climb out) and a short list of credential directories is
    refused outright, because this same agent talks to strangers on WhatsApp.
  * Only the owner ever gets these. `files` and `shell` are granted to the
    owner trust level and to nobody else, so a stranger is never even told the
    tools exist -- specs_for() filters them out of the schema list entirely.

run_command is registered as destructive, so it parks on a confirmation every
single time and the person sees the exact command before it runs. That button
is the real safety boundary here, not the path checks.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import shutil
from pathlib import Path

from presence.config import settings
from presence.tools.registry import ToolContext, tool

# Directories that hold credentials. Nothing in here is worth the risk of an
# agent reading one out loud in a chat, so they are refused even to the owner.
PROTECTED_PARTS = {
    ".ssh", ".aws", ".gnupg", ".kube", ".docker", ".netrc",
    "Keychains", ".password-store", ".config/gcloud",
}

MAX_READ_BYTES = 100_000
MAX_OUTPUT = 5_000
MAX_ENTRIES = 200
DEFAULT_TIMEOUT = 120
MAX_TIMEOUT = 600


def _root() -> Path:
    return Path(settings.workspace_root).expanduser().resolve()


def _resolve(path: str) -> tuple[Path | None, str]:
    """Path under the workspace root, or (None, why not).

    Accepts absolute paths, ~ paths and paths relative to the root, because the
    model will use all three. resolve() runs before the containment check so a
    symlink pointing outside cannot be used to step out.
    """
    root = _root()
    raw = (path or ".").strip()
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve()
    except OSError as e:
        return None, f"That path could not be resolved: {e}."

    if resolved != root and root not in resolved.parents:
        return None, (f"'{raw}' is outside the workspace ({root}). Work inside it, or "
                      f"tell the user to move WORKSPACE_ROOT if they meant somewhere else.")
    if any(part in PROTECTED_PARTS for part in resolved.parts):
        return None, f"'{raw}' is in a credentials directory. That is off limits."
    return resolved, ""


def _rel(p: Path) -> str:
    """Path as the person would say it, absolute when that is clearer."""
    root = _root()
    try:
        return str(p.relative_to(root)) or "."
    except ValueError:
        return str(p)


@tool(risk="read", scopes={"files"})
async def list_dir(ctx: ToolContext, path: str = ".") -> str:
    """List the files and folders at a path.

    Use this before writing anything, to see what is already there. The path is
    relative to the workspace root, so "Desktop" means the Desktop folder and
    "." means the root itself.
    """
    target, why = _resolve(path)
    if target is None:
        return why
    if not target.exists():
        return f"Nothing exists at {_rel(target)}."
    if not target.is_dir():
        return f"{_rel(target)} is a file, not a folder. Use read_file for it."

    try:
        entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError as e:
        return f"Could not read {_rel(target)}: {e}."
    if not entries:
        return f"{_rel(target)} is empty."

    lines = []
    for entry in entries[:MAX_ENTRIES]:
        try:
            size = "" if entry.is_dir() else f"  {entry.stat().st_size:,} bytes"
        except OSError:
            size = ""
        lines.append(f"{'📁 ' if entry.is_dir() else '   '}{entry.name}{size}")
    more = (f"\n…and {len(entries) - MAX_ENTRIES} more."
            if len(entries) > MAX_ENTRIES else "")
    return f"{_rel(target)} ({len(entries)} items):\n" + "\n".join(lines) + more


@tool(risk="read", scopes={"files"})
async def read_file(ctx: ToolContext, path: str) -> str:
    """Read a text file. Use it to check what is in a file before changing it."""
    target, why = _resolve(path)
    if target is None:
        return why
    if not target.exists():
        return f"There is no file at {_rel(target)}."
    if target.is_dir():
        return f"{_rel(target)} is a folder. Use list_dir for it."

    try:
        raw = target.read_bytes()[: MAX_READ_BYTES + 1]
    except OSError as e:
        return f"Could not read {_rel(target)}: {e}."

    truncated = len(raw) > MAX_READ_BYTES
    try:
        text = raw[:MAX_READ_BYTES].decode("utf-8")
    except UnicodeDecodeError:
        return f"{_rel(target)} is a binary file, so there is no text to show."
    if not text.strip():
        return f"{_rel(target)} is empty."
    tail = "\n…truncated, the file is longer than this." if truncated else ""
    return f"{_rel(target)}:\n{text}{tail}"


@tool(risk="write", scopes={"files"})
async def write_file(ctx: ToolContext, path: str, content: str,
                     overwrite: bool = False) -> str:
    """Write a text file, creating any folders it needs.

    Refuses to replace a file that already exists unless overwrite is true, so
    read it first and pass the full new contents -- this replaces the whole
    file rather than appending to it.
    """
    target, why = _resolve(path)
    if target is None:
        return why
    if target.is_dir():
        return f"{_rel(target)} is a folder, so it cannot be written as a file."
    if target.exists() and not overwrite:
        return (f"{_rel(target)} already exists. Read it first, then call write_file "
                f"again with overwrite=true if you really mean to replace it.")

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except OSError as e:
        return f"Could not write {_rel(target)}: {e}."
    verb = "Replaced" if overwrite else "Wrote"
    return f"{verb} {target} ({len(content):,} characters)."


@tool(
    risk="destructive",
    scopes={"shell"},
    confirm=("Run this on your machine?\n\n{command}\n\n"
             "Working directory: {cwd}\n\nPress Yes and I'll run it."),
)
async def run_command(ctx: ToolContext, command: str, cwd: str = ".",
                      timeout: int = DEFAULT_TIMEOUT) -> str:
    """Run a shell command on the user's machine and return its output.

    This is how real work gets done: creating a project, installing packages,
    running a build, checking git. The person sees the exact command and has to
    approve it before it runs, so write the command you actually mean.

    cwd is where it runs, relative to the workspace root -- pass "Desktop" to
    work on the Desktop. Prefer one clear command per call so that a failure
    says which step failed. For anything long-running, raise timeout (seconds).
    """
    command = (command or "").strip()
    if not command:
        return "No command was given."

    where, why = _resolve(cwd)
    if where is None:
        return why
    if not where.is_dir():
        return (f"{_rel(where)} is not a folder that exists, so there is nowhere to run "
                f"this. Create it first, or pass a different cwd.")

    try:
        limit = max(1, min(int(timeout or DEFAULT_TIMEOUT), MAX_TIMEOUT))
    except (TypeError, ValueError):
        limit = DEFAULT_TIMEOUT

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(where),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
    except OSError as e:
        return f"Could not start that command: {e}."

    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=limit)
    except TimeoutError:
        # Leaving it running would hold a subprocess for the life of the process
        # and the model would never learn what happened.
        proc.kill()
        await proc.wait()
        return (f"`{command}` was still running after {limit}s, so I stopped it. "
                f"If it genuinely needs longer, call it again with a bigger timeout.")

    text = (out or b"").decode("utf-8", errors="replace").strip()
    if len(text) > MAX_OUTPUT:
        text = text[:MAX_OUTPUT] + f"\n…truncated, {len(text) - MAX_OUTPUT} characters omitted."

    status = "succeeded" if proc.returncode == 0 else f"failed (exit {proc.returncode})"
    body = text or "(no output)"
    return f"`{command}` {status} in {where}.\n\n{body}"


@tool(risk="read", scopes={"shell"})
async def which(ctx: ToolContext, program: str) -> str:
    """Check whether a command line program is installed before trying to use it.

    Saves a confirmation round trip on a command that was never going to work --
    check for python3, django-admin, node, git and so on.
    """
    name = (program or "").strip()
    if not name:
        return "Name a program to look for."
    # shlex.split in case the model passes "python3 --version" by mistake.
    parts = shlex.split(name) or [name]
    found = shutil.which(parts[0])
    return (f"{parts[0]} is installed at {found}." if found
            else f"{parts[0]} is not installed, or not on PATH.")
