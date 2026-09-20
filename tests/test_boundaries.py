"""The invariant the whole harness rests on.

If a surface SDK leaks into the core, "one agent, every surface" quietly stops
being true and nobody notices until the second surface is added.
"""

from __future__ import annotations

import pathlib
import re

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "presence"

# Everything except adapters/ and render/ must be surface-agnostic.
CORE_DIRS = ["core", "agent", "providers", "tools", "store", "scheduler", "gateway",
             "media"]
SURFACE_SDKS = {"telegram", "slack", "slack_sdk", "fastapi", "frappe", "discord",
                "twilio", "neonize", "segno"}

IMPORT_RE = re.compile(r"^\s*(?:from|import)\s+([\w.]+)", re.M)


def test_core_never_imports_a_surface_sdk() -> None:
    offences = [
        f"{path.relative_to(SRC)} imports {module}"
        for d in CORE_DIRS
        for path in (SRC / d).rglob("*.py")
        for module in IMPORT_RE.findall(path.read_text())
        if module.split(".")[0] in SURFACE_SDKS
    ]
    assert not offences, "surface SDK leaked into the core:\n" + "\n".join(offences)


def test_every_adapter_satisfies_the_protocol() -> None:
    """A surface is five methods and two attributes. Nothing else."""
    from presence.adapters.cli import CLIAdapter
    from presence.adapters.telegram import TelegramAdapter
    from presence.adapters.whatsapp import WhatsAppAdapter

    for cls in (CLIAdapter, TelegramAdapter, WhatsAppAdapter):
        assert isinstance(cls.surface, str), cls
        assert cls.capabilities.max_chars > 0, cls
        for method in ("start", "send", "edit", "typing", "stop"):
            assert callable(getattr(cls, method, None)), f"{cls.__name__}.{method}"


def test_capability_descriptions_reach_the_model() -> None:
    """describe() is injected into every system prompt, so it must say something."""
    from presence.core.capabilities import BY_SURFACE

    for name, caps in BY_SURFACE.items():
        text = caps.describe()
        assert str(caps.max_chars) in text, name
        assert len(text) > 60, name
