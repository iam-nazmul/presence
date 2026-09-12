"""What a surface can actually do.

This is not only renderer config -- it is rendered into the system prompt. A
model told it is on WhatsApp writes differently than one told it is in a
terminal, and that paragraph is most of what "native" means.

FROZEN CONTRACT. Additive changes only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class SurfaceCapabilities:
    name: str
    max_chars: int
    markdown: Literal["none", "basic", "full"]
    supports_blocks: bool = False
    supports_buttons: bool = False
    max_buttons: int = 0
    supports_files: bool = False
    supports_images: bool = False
    supports_voice: bool = False
    supports_streaming: bool = False
    stream_interval_s: float = 1.0
    supports_typing: bool = False
    supports_threads: bool = False
    latency_budget_s: float = 8.0

    def describe(self) -> str:
        """The paragraph the model reads. Keep it short; it is in every prompt."""
        bits = [f"You are replying on {self.name}."]
        if self.markdown == "none":
            bits.append("It does NOT render markdown -- write plain sentences, no ** or ` marks.")
        elif self.markdown == "basic":
            bits.append("It renders basic markdown only (bold, italic, code, links)."
                        " No tables, no headings.")
        else:
            bits.append("It renders full markdown including tables and headings.")
        bits.append(f"Keep each reply under {self.max_chars} characters.")
        if self.supports_buttons:
            bits.append(f"You may offer up to {self.max_buttons} choices as buttons.")
        else:
            bits.append("There are no buttons; offer choices as a short numbered list.")
        if not self.supports_streaming:
            bits.append("Replies arrive all at once, so lead with the answer.")
        return " ".join(bits)


CLI = SurfaceCapabilities(
    name="a terminal",
    max_chars=100_000,
    markdown="basic",
    supports_buttons=True,
    max_buttons=9,
    supports_files=True,
    supports_streaming=True,
    stream_interval_s=0.0,
    latency_budget_s=60.0,
)

TELEGRAM = SurfaceCapabilities(
    name="Telegram",
    max_chars=4096,
    markdown="basic",
    supports_buttons=True,
    max_buttons=8,
    supports_files=True,
    supports_images=True,
    supports_voice=True,
    supports_streaming=True,
    stream_interval_s=0.9,
    supports_typing=True,
    latency_budget_s=10.0,
)

SLACK = SurfaceCapabilities(
    name="Slack",
    max_chars=3000,
    markdown="basic",
    supports_blocks=True,
    supports_buttons=True,
    max_buttons=5,
    supports_files=True,
    supports_images=True,
    supports_streaming=True,
    stream_interval_s=1.0,
    supports_threads=True,
    latency_budget_s=10.0,
)

WHATSAPP = SurfaceCapabilities(
    name="WhatsApp",
    max_chars=4096,
    markdown="none",
    supports_buttons=True,
    max_buttons=3,
    supports_images=True,
    supports_voice=True,
    supports_streaming=False,
    latency_budget_s=15.0,
)

WEBAPP = SurfaceCapabilities(
    name="an in-app assistant panel",
    max_chars=100_000,
    markdown="full",
    supports_blocks=True,
    supports_buttons=True,
    max_buttons=6,
    supports_files=True,
    supports_images=True,
    supports_streaming=True,
    stream_interval_s=0.0,
    latency_budget_s=30.0,
)

ERPNEXT = SurfaceCapabilities(
    name="a document comment thread",
    max_chars=20_000,
    markdown="full",
    supports_threads=True,
    latency_budget_s=90.0,
)

SYSTEM = SurfaceCapabilities(
    name="a scheduled background trigger (nobody is waiting at a keyboard)",
    max_chars=4096,
    markdown="basic",
    latency_budget_s=600.0,
)

BY_SURFACE = {
    "cli": CLI,
    "telegram": TELEGRAM,
    "slack": SLACK,
    "whatsapp": WHATSAPP,
    "webapp": WEBAPP,
    "erpnext": ERPNEXT,
    "system": SYSTEM,
}
