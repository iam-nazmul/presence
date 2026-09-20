"""Bytes of a file in, text the model can actually read out.

A document someone sends is not an attachment to be acknowledged -- it is the
thing they are asking about. "I cannot read attached PDFs, please paste the key
text" is the reply that ends the conversation, and it was never true: a PDF with
a text layer is a zip of strings, and pulling them out is a library call.

Nothing here knows which surface the file arrived on, and nothing here raises.
A file that will not open is still a conversation, so every failure comes back
as a sentence the model can repeat and act on -- a scan needs re-sending as a
photo, a spreadsheet needs exporting as CSV, and those are different asks.
"""

from __future__ import annotations

import html
import logging
import re
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath

from presence.config import settings

log = logging.getLogger("presence.media")

# A 200-page contract would eat the context window and push the conversation it
# arrived in out of the other end. Pages first, then characters.
MAX_PDF_PAGES = 60

# Mime types lie -- WhatsApp sends application/octet-stream for anything it does
# not recognise -- so the sniff on the first bytes wins over both of these.
TEXT_MIMES = (
    "text/", "application/json", "application/xml", "application/yaml",
    "application/x-yaml", "application/csv", "application/javascript",
    "application/x-sh", "application/sql",
)
TEXT_SUFFIXES = (
    ".txt", ".md", ".markdown", ".rst", ".csv", ".tsv", ".json", ".yaml", ".yml",
    ".xml", ".html", ".htm", ".log", ".ini", ".cfg", ".conf", ".toml", ".env",
    ".sql", ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".kt", ".c", ".h",
    ".cpp", ".cs", ".go", ".rs", ".rb", ".php", ".sh", ".css", ".srt", ".vtt",
)

PDF_MAGIC = b"%PDF"
ZIP_MAGIC = b"PK\x03\x04"

# Word puts one paragraph per <w:p>; without this every heading runs into the
# sentence after it and a contract becomes one unbroken line.
_PARA_END = re.compile(r"</w:p>")
_BREAK = re.compile(r"<w:(?:br|tab)[^>]*/>")
_TAGS = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class Extracted:
    """Either the text, or one sentence saying why there is none."""

    text: str = ""
    problem: str = ""


def extract(data: bytes, mime: str | None = None, name: str | None = None) -> Extracted:
    """Read a file. Always answers; never raises."""
    if not data:
        return Extracted(problem="the file arrived empty")

    suffix = PurePosixPath(name or "").suffix.lower()
    mime = (mime or "").split(";")[0].strip().lower()

    try:
        if data.startswith(PDF_MAGIC) or mime == "application/pdf" or suffix == ".pdf":
            return _truncate(_pdf(data))
        if data.startswith(ZIP_MAGIC) and (suffix == ".docx" or "wordprocessing" in mime):
            return _truncate(_docx(data))
        if _looks_textual(data, mime, suffix):
            return _truncate(_plain(data))
    except Exception as e:  # a malformed file is a message, not an incident
        log.warning("could not read %s (%s): %s", name or "file", mime or "?", e)
        return Extracted(problem=f"the file would not open ({type(e).__name__})")

    return Extracted(problem=_unsupported(suffix, mime))


def _pdf(data: bytes) -> Extracted:
    try:
        from pypdf import PdfReader
    except ImportError:  # pragma: no cover - pypdf is a hard dependency
        return Extracted(problem="PDF reading is not installed on this machine")

    reader = PdfReader(BytesIO(data))
    if reader.is_encrypted:
        # An empty password covers the common case: a file locked only against
        # editing. A real password is not ours to guess.
        try:
            opened = reader.decrypt("")
        except Exception:
            opened = 0
        if not opened:
            return Extracted(problem="the PDF is password protected")

    pages = reader.pages[:MAX_PDF_PAGES]
    text = "\n\n".join(filter(None, ((p.extract_text() or "").strip() for p in pages)))
    if not text:
        # Nothing to do about this one in code: the page images would have to go
        # to a vision model, and saying which failure it is lets them re-send
        # the page as a photo, which does work.
        return Extracted(problem="the PDF has no text layer -- it is a scan or images")

    if len(reader.pages) > MAX_PDF_PAGES:
        text += f"\n\n[first {MAX_PDF_PAGES} of {len(reader.pages)} pages]"
    return Extracted(text=text)


def _docx(data: bytes) -> Extracted:
    """Word, without a dependency: .docx is a zip with the text in one XML part."""
    with zipfile.ZipFile(BytesIO(data)) as z:
        if "word/document.xml" not in z.namelist():
            return Extracted(problem="the Word file has no readable document part")
        xml = z.read("word/document.xml").decode("utf-8", "replace")

    xml = _BREAK.sub(" ", _PARA_END.sub("\n", xml))
    text = html.unescape(_TAGS.sub("", xml)).strip()
    if not text:
        return Extracted(problem="the Word file has no text in it")
    return Extracted(text=re.sub(r"\n{3,}", "\n\n", text))


def _plain(data: bytes) -> Extracted:
    text = data.decode("utf-8", "replace").strip()
    return Extracted(text=text) if text else Extracted(problem="the file is empty")


def _looks_textual(data: bytes, mime: str, suffix: str) -> bool:
    """Is this readable as text, whatever the sender's phone called it?

    A NUL byte in the first kilobyte means binary every time -- no encoding this
    decodes to puts one there -- and that check is what keeps a mislabelled
    image out of the context window as four pages of replacement characters.
    """
    if b"\x00" in data[:1024]:
        return False
    return (suffix in TEXT_SUFFIXES
            or any(mime.startswith(m) for m in TEXT_MIMES)
            or (not mime and not suffix))


def _unsupported(suffix: str, mime: str) -> str:
    """Why this one cannot be read, in terms of what they could do instead."""
    kind = suffix.lstrip(".") or mime or "that kind of file"
    if suffix in (".xlsx", ".xls", ".ods"):
        return f"{kind} files are not readable here -- a CSV export would be"
    if suffix in (".doc", ".rtf"):
        return f"{kind} is the old Word format -- a PDF or .docx of it would work"
    if suffix in (".zip", ".rar", ".7z", ".tar", ".gz"):
        return "it is an archive -- the file inside it can be sent on its own"
    if suffix in (".pptx", ".ppt", ".key"):
        return f"{kind} slides are not readable here -- a PDF export would be"
    return f"{kind} is not a format that can be read here"


def _truncate(found: Extracted) -> Extracted:
    limit = max(settings.attachment_max_chars, 500)
    if not found.text or len(found.text) <= limit:
        return found
    # Cut on a line so the model is not handed half a sentence, and say so --
    # answering "the total is" off a truncated table is worse than not knowing.
    cut = found.text[:limit].rsplit("\n", 1)[0] or found.text[:limit]
    return Extracted(text=f"{cut}\n\n[cut here -- the file is longer than this]")
