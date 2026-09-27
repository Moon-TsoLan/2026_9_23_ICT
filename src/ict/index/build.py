"""Scan work/attachments/<id>/ and write index.json. This does not open contest zips."""

from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path

from ict.config import ATTACHMENTS_ROOT, LOW_TEXT_CHARS_PER_PAGE
from ict.ids import format_file_id
from ict.schemas import AttachmentIndex, IndexedFile
from ict.state import write_json

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp"}
SKIP_NAMES = {".extract_complete"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _probe_pdf(path: Path) -> tuple[int | None, float | None, str, str | None]:
    from ict.documents import markdown_path_for_pdf

    markdown = markdown_path_for_pdf(path)
    if markdown is not None and markdown.stat().st_size > 0:
        return None, None, "text_extractable", "markdown"
    import pymupdf

    try:
        document = pymupdf.open(path)
    except Exception as exc:
        hint = "encrypted" if "encrypt" in str(exc).lower() else "parse_failed"
        return None, None, "parse_failed", hint
    if document.is_encrypted:
        document.close()
        return None, None, "parse_failed", "encrypted"
    chars = 0
    for page in document:
        chars += len(page.get_text() or "")
    pages = document.page_count
    document.close()
    density = None if not pages else chars / pages
    if density is not None and density < LOW_TEXT_CHARS_PER_PAGE:
        return pages, density, "low_text", None
    return pages, density, "text_extractable", None


def _probe_docx(path: Path) -> tuple[str, str | None]:
    try:
        import docx

        document = docx.Document(str(path))
        text = "\n".join(paragraph.text for paragraph in document.paragraphs)
        return ("text_extractable" if text.strip() else "low_text"), None
    except Exception:
        return "parse_failed", None


def build_index(announcement_id: str, root: Path | None = None) -> AttachmentIndex:
    directory = (root or ATTACHMENTS_ROOT) / announcement_id
    if not directory.exists():
        return AttachmentIndex(announcement_id=announcement_id, attachment_directory=None, files=[])
    files: list[IndexedFile] = []
    seen: dict[str, str] = {}
    seq = 1
    paths = sorted(path for path in directory.rglob("*") if path.is_file() and path.name not in SKIP_NAMES)
    for path in paths:
        digest = _sha256(path)
        if digest in seen:
            continue
        suffix = path.suffix.lower()
        relative = path.relative_to(directory).as_posix()
        file_id = format_file_id(seq)
        seen[digest] = file_id
        seq += 1
        page_count = None
        density = None
        hint = None
        if suffix in IMAGE_SUFFIXES:
            readability = "unsupported"
        elif suffix == ".pdf":
            page_count, density, readability, hint = _probe_pdf(path)
        elif suffix == ".docx":
            readability, hint = _probe_docx(path)
        elif suffix in {".doc", ".xls", ".xlsx"}:
            readability = "text_extractable"
        else:
            readability = "unsupported" if suffix in {".zip", ".rar", ".7z"} else "unknown"
        payload = {
            "file_id": file_id,
            "display_name": path.name,
            "relative_path": relative,
            "mime_type": mimetypes.guess_type(path.name)[0],
            "extension": suffix,
            "page_count": page_count,
            "text_density": density,
            "readability": readability,
        }
        if hint:
            payload["failure_hint"] = hint
        files.append(IndexedFile.model_validate(payload))
    index = AttachmentIndex(
        announcement_id=announcement_id,
        attachment_directory=str(directory),
        files=files,
    )
    write_json(directory / "index.json", index)
    return index


def load_index(announcement_id: str, root: Path | None = None) -> AttachmentIndex | None:
    directory = (root or ATTACHMENTS_ROOT) / announcement_id
    path = directory / "index.json"
    if not path.exists():
        return None
    return AttachmentIndex.model_validate_json(path.read_text(encoding="utf-8"))
