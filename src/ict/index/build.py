"""Attachment index for one announcement, built from file bytes by ict.parse.census.

This replaces the old probe that asked the PDF text layer and trusted extensions, and it
drops the `a markdown twin exists` shortcut entirely: an extension of .docx can hide an OLE
file, and text density cannot see a table that lives inside a picture.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

from ict.config import ATTACHMENTS_ROOT
from ict.ids import format_file_id
from ict.parse.census import FileEntry, build_census
from ict.schemas import AttachmentIndex, IndexedFile
from ict.state import write_json

READABLE_BY_FORMAT = {"pdf": "text_extractable", "docx": "text_extractable", "xlsx": "text_extractable", "text": "text_extractable"}


def _readability(entry: FileEntry) -> str:
    if entry.note.startswith("pdf_open_failed") or entry.note == "encrypted_pdf":
        return "parse_failed"
    if entry.fmt == "image":
        return "ocr_needed"
    if entry.needs_normalisation:
        return "needs_normalisation"
    return READABLE_BY_FORMAT.get(entry.fmt, "unknown")


def _density(entry: FileEntry) -> float | None:
    # Cheap and only for what we can read natively; the gate decides the rest.
    if entry.fmt in {"pdf", "docx", "xlsx"} and not entry.needs_normalisation:
        try:
            from ict.parse.peek import peek_entry

            peek = peek_entry(entry)
            pages = entry.pages or 1
            return peek.full_chars / max(1, pages) if peek.readable else None
        except Exception:  # noqa: BLE001 - a failed density guess never blocks indexing
            return None
    return None


def to_indexed(entry: FileEntry, seq: int) -> IndexedFile:
    return IndexedFile(
        file_id=entry.file_id or format_file_id(seq),
        display_name=entry.name,
        relative_path=entry.relative_path,
        mime_type=mimetypes.guess_type(entry.name)[0],
        extension=entry.declared_ext,
        page_count=entry.pages,
        text_density=_density(entry),
        readability=_readability(entry),
        fmt=entry.fmt,
        note=entry.note,
        digest=entry.digest,
        native_readable=entry.native_readable,
        needs_normalisation=entry.needs_normalisation,
    )


def build_index(announcement_id: str, root: Path | None = None) -> AttachmentIndex:
    directory = (root or ATTACHMENTS_ROOT) / announcement_id
    if not directory.exists():
        return AttachmentIndex(announcement_id=announcement_id, attachment_directory=None, files=[])
    entries, duplicates = build_census(directory)
    files = [to_indexed(entry, index) for index, entry in enumerate(entries, start=1)]
    index = AttachmentIndex(announcement_id=announcement_id, attachment_directory=str(directory), files=files)
    payload = index.model_dump()
    payload["duplicate_files"] = duplicates
    write_json(directory / "index.json", payload)
    return index


def load_index(announcement_id: str, root: Path | None = None) -> AttachmentIndex | None:
    directory = (root or ATTACHMENTS_ROOT) / announcement_id
    path = directory / "index.json"
    if not path.exists():
        return None
    return AttachmentIndex.model_validate_json(path.read_text(encoding="utf-8"))
