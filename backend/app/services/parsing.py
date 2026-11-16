"""Turn an uploaded file into a list of page texts. Synchronous: call from a worker thread."""

from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PyPdfError

PDF_MAGIC = b"%PDF-"


class IngestError(Exception):
    """A permanent, user-facing reason a document cannot be ingested."""


def detect_kind(filename: str, head: bytes) -> str | None:
    """Return 'pdf' or 'text' from the extension and first bytes, or None if unsupported."""
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        return "pdf" if head.startswith(PDF_MAGIC) else None
    if suffix in {".txt", ".md"}:
        return "text" if b"\x00" not in head else None
    return None


def extract_pages(path: Path, kind: str) -> list[str]:
    """Return one string per page (plain-text files count as a single page)."""
    if kind == "text":
        try:
            return [path.read_text(encoding="utf-8")]
        except UnicodeDecodeError:
            raise IngestError("Text file is not valid UTF-8") from None
    try:
        reader = PdfReader(path)
        if reader.is_encrypted:
            raise IngestError("PDF is password-protected")
        return [page.extract_text() or "" for page in reader.pages]
    except PyPdfError:
        raise IngestError("PDF could not be read (file may be corrupt)") from None
