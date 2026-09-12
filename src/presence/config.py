"""One source of truth for environment configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parents[2]


def _split(value: str) -> list[str]:
    return [p.strip() for p in value.split(",") if p.strip()]


def _flag(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    # --- model -------------------------------------------------------------
    provider: str = os.getenv("PROVIDER", "ollama")
    base_url: str = ""
    api_key: str = ""
    model_main: str = os.getenv("MODEL_MAIN", "qwen3.5:9b")
    model_fast: str = os.getenv("MODEL_FAST", "qwen3.5:4b")
    num_ctx: int = int(os.getenv("NUM_CTX", "32768"))
    temperature: float = float(os.getenv("TEMPERATURE", "0.3"))
    # Real work is multi-step -- check a folder, run a command, read the error,
    # fix it -- and every confirmation spends a turn too. Eight ran out mid-task.
    max_turns: int = int(os.getenv("MAX_TURNS", "16"))
    keep_alive: str = os.getenv("KEEP_ALIVE", "2h")

    # --- storage -----------------------------------------------------------
    db_path: str = os.getenv("DB_PATH", str(ROOT / "presence.db"))

    # --- workspace ---------------------------------------------------------
    # Everything the file and shell tools touch has to sit under this. Home by
    # default so "make a Django project on my Desktop" works out of the box;
    # narrow it to one projects folder if you want a tighter blast radius.
    workspace_root: str = os.getenv("WORKSPACE_ROOT", str(Path.home()))

    # --- portal api --------------------------------------------------------
    # Loopback by default and not negotiable without an explicit override:
    # leads carry phone numbers and ID card numbers. Set API_PORT=0 to disable.
    api_host: str = os.getenv("API_HOST", "127.0.0.1")
    api_port: int = int(os.getenv("API_PORT", "8765"))

    # --- surfaces ----------------------------------------------------------
    telegram_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")

    # WhatsApp links a personal account as a device, so there is no token to
    # gate it on the way TELEGRAM_BOT_TOKEN gates Telegram -- it is opt-in by
    # this flag, or by running `presence whatsapp` explicitly.
    whatsapp_enabled: bool = _flag("WHATSAPP_ENABLED")
    # The linked-device credential. Treat this file like a password.
    whatsapp_session: str = os.getenv("WHATSAPP_SESSION", str(ROOT / "whatsapp-session.db"))
    # Numbers the agent will answer. Empty means everyone who has your number,
    # which on a personal account is usually not what you want.
    whatsapp_allowed: list[str] = field(
        default_factory=lambda: _split(os.getenv("WHATSAPP_ALLOWED", ""))
    )
    whatsapp_groups: bool = _flag("WHATSAPP_GROUPS")
    whatsapp_self_chat: bool = _flag("WHATSAPP_SELF_CHAT", True)
    whatsapp_mark_read: bool = _flag("WHATSAPP_MARK_READ", True)
    # Pair by typing a code on the phone instead of scanning a QR.
    whatsapp_pair_phone: str = os.getenv("WHATSAPP_PAIR_PHONE", "")

    # --- tools -------------------------------------------------------------
    exa_api_key: str = os.getenv("EXA_API_KEY", "")

    # --- identity ----------------------------------------------------------
    # OWNER_IDENTITIES=telegram:12345,slack:U04AB,cli:local
    owner_identities: list[str] = field(
        default_factory=lambda: _split(os.getenv("OWNER_IDENTITIES", "cli:local"))
    )
    owner_name: str = os.getenv("OWNER_NAME", "you")
    preferred_surface: str = os.getenv("PREFERRED_SURFACE", "")

    def __post_init__(self) -> None:
        presets = {
            "ollama": (
                os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
                os.getenv("OLLAMA_API_KEY", "ollama"),
            ),
            "openai": ("https://api.openai.com/v1", os.getenv("OPENAI_API_KEY", "")),
            "openrouter": (
                "https://openrouter.ai/api/v1",
                os.getenv("OPENROUTER_API_KEY", ""),
            ),
        }
        url, key = presets.get(self.provider, presets["ollama"])
        self.base_url = os.getenv("BASE_URL", url)
        self.api_key = os.getenv("API_KEY", key) or "none"


settings = Settings()
