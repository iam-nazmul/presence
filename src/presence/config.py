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
    max_turns: int = int(os.getenv("MAX_TURNS", "8"))
    keep_alive: str = os.getenv("KEEP_ALIVE", "2h")

    # --- storage -----------------------------------------------------------
    db_path: str = os.getenv("DB_PATH", str(ROOT / "presence.db"))

    # --- portal api --------------------------------------------------------
    # Loopback by default and not negotiable without an explicit override:
    # leads carry phone numbers and ID card numbers. Set API_PORT=0 to disable.
    api_host: str = os.getenv("API_HOST", "127.0.0.1")
    api_port: int = int(os.getenv("API_PORT", "8765"))

    # --- surfaces ----------------------------------------------------------
    telegram_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")

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
