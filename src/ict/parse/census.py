"""Content-addressed census of one attachment directory.

Extensions lie in this corpus: 127 OLE Word files are named `.docx`, some files carry no
extension at all, and some are formats we can never open. Every downstream decision keys on
the sniffed container plus the sha256 digest, and identical bytes collapse into one entry.
"""

from __future__ import annotations

import hashlib
import zipfile
from dataclasses import dataclass
from pathlib import Path

PDF_MAGIC = b"%PDF"
ZIP_MAGIC = b"PK\x03\x04"
OLE_MAGIC = b"\xd0\xcf\x11\xe0"
IMAGE_MAGIC = {b"\x89PNG": "png", b"\xff\xd8\xff": "jpg", b"GIF8": "gif", b"BM": "bmp"}
SKIP_NAMES = {".extract_complete", "index.json", "_extract_report.json", "peek.json"}

NATIVE = {"pdf", "docx", "xlsx", "text"}
NEEDS_NORMALISATION = {"ole", "image", "zip", "unknown"}


def digest_of(path: Path) -> str:
    hash_ = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            hash_.update(block)
    return hash_.hexdigest()


def sniff(path: Path) -> tuple[str, str]:
    """Return (format, note) decided from file bytes only."""
    try:
        with path.open("rb") as handle:
            head = handle.read(16)
    except OSError:
        return "unknown", "unreadable"
    if head.startswith(PDF_MAGIC):
        return "pdf", ""
    if head.startswith(ZIP_MAGIC):
        try:
            names = set(zipfile.ZipFile(path).namelist())
        except Exception:
            return "zip", "broken_zip"
        if "word/document.xml" in names:
            return "docx", ""
        if "xl/workbook.xml" in names:
            return "xlsx", ""
        return "zip", "container_zip"
    if head.startswith(OLE_MAGIC):
        return "ole", "legacy_office"
    for marker, name in IMAGE_MAGIC.items():
        if head.startswith(marker):
            return "image", name
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image", "webp"
    if head[:1] in (b"{", b"[") or head[:14].lower().startswith((b"<!doctype", b"<html")):
        return "text", "json_or_html"
    return "unknown", "magic_" + head[:4].hex()


def pdf_pages(path: Path) -> tuple[int | None, str]:
    try:
        import pymupdf
    except ImportError:  # pragma: no cover
        return None, "pymupdf_missing"
    try:
        document = pymupdf.open(str(path))
    except Exception as exc:
        return None, "pdf_open_failed:" + type(exc).__name__
    try:
        if document.is_encrypted:
            return None, "encrypted_pdf"
        return document.page_count, ""
    finally:
        document.close()


@dataclass
class FileEntry:
    file_id: str
    name: str
    relative_path: str
    path: Path
    size_bytes: int
    declared_ext: str
    fmt: str
    note: str
    digest: str
    pages: int | None = None

    @property
    def native_readable(self) -> bool:
        return self.fmt in NATIVE and not self.note.startswith("pdf_open_failed")

    @property
    def needs_normalisation(self) -> bool:
        return self.fmt in NEEDS_NORMALISATION or self.note.startswith("pdf_open_failed")

    def short(self) -> dict:
        return {"file_id": self.file_id, "name": self.name, "format": self.fmt, "note": self.note,
                "bytes": self.size_bytes, "pages": self.pages, "declared_ext": self.declared_ext,
                "digest12": self.digest[:12]}


def build_census(directory: Path, start_seq: int = 1) -> tuple[list[FileEntry], list[dict]]:
    """Inventory one announcement's attachments; duplicates by content are reported, not parsed."""
    entries: list[FileEntry] = []
    duplicates: list[dict] = []
    by_digest: dict[str, FileEntry] = {}
    root = Path(directory)
    seq = start_seq
    for path in sorted(p for p in root.rglob("*") if p.is_file() and p.name not in SKIP_NAMES):
        digest = digest_of(path)
        if digest in by_digest:
            duplicates.append({"name": path.name, "duplicate_of": by_digest[digest].file_id,
                               "digest12": digest[:12]})
            continue
        fmt, note = sniff(path)
        pages = None
        if fmt == "pdf":
            pages, note = pdf_pages(path)
            if pages is None and not note:
                note = "pages_unknown"
        entry = FileEntry(file_id="a%03d" % seq, name=path.name,
                          relative_path=path.relative_to(root).as_posix(), path=path,
                          size_bytes=path.stat().st_size, declared_ext=path.suffix.lower(),
                          fmt=fmt, note=note, digest=digest, pages=pages)
        entries.append(entry)
        by_digest[digest] = entry
        seq += 1
    return entries, duplicates
