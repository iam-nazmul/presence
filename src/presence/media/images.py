"""Photos, cut down to something a model can actually look at.

A phone sends twelve megapixels. A vision model tiles that into thousands of
image tokens, and on a local box the turn either crawls past the request
timeout or runs the context window out -- both of which reach the person as
"something went wrong", after they waited two minutes for it. A business card
is completely legible at 1280px, and that is a twentieth of the pixels.

The other half of this is rotation. Phones record orientation in EXIF rather
than in the pixels, so a card photographed in portrait arrives sideways, and a
model reading sideways text gets the digits wrong rather than failing outright
-- which is worse, because nobody checks a phone number that looks plausible.
"""

from __future__ import annotations

import io
import logging

from presence.config import settings

log = logging.getLogger("presence.media")

# Below this there is nothing to gain and re-encoding only costs quality.
MIN_EDGE = 256


def shrink(data: bytes, mime: str | None = None) -> tuple[bytes, str]:
    """(bytes, mime) ready for the model. The original on any failure."""
    original = (data, mime or "image/jpeg")
    try:
        from PIL import Image, ImageOps
    except ImportError:  # pragma: no cover - pillow is a hard dependency
        return original

    try:
        with Image.open(io.BytesIO(data)) as img:
            img = ImageOps.exif_transpose(img) or img
            edge = max(settings.image_max_edge, MIN_EDGE)
            if max(img.size) > edge:
                img.thumbnail((edge, edge), Image.LANCZOS)
            img = _flatten(img)

            out = io.BytesIO()
            img.save(out, "JPEG", quality=max(min(settings.image_quality, 95), 40),
                     optimize=True)
    except Exception as e:
        # A file that will not open here is still a file the model might make
        # sense of, so it goes on as it arrived.
        log.warning("could not resize an image (%s): %s", mime or "?", e)
        return original

    smaller = out.getvalue()
    if len(smaller) >= len(data):
        return original  # already tighter than anything re-encoding would give
    log.debug("image %d -> %d bytes", len(data), len(smaller))
    return smaller, "image/jpeg"


def _flatten(img):
    """RGB, on white where there was transparency.

    convert("RGB") alone composites onto black, which turns a logo with a
    transparent background into a black rectangle.
    """
    if img.mode in ("RGBA", "LA", "P"):
        from PIL import Image

        img = img.convert("RGBA")
        canvas = Image.new("RGB", img.size, (255, 255, 255))
        canvas.paste(img, mask=img.split()[-1])
        return canvas
    return img if img.mode in ("RGB", "L") else img.convert("RGB")
