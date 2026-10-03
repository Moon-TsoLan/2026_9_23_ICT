"""Cheap native text view of a file, used by the screen gate.

Measured on the gold corpus: python-docx reads a 60-page tender document in ~100 ms and
returns 40k+ characters including table cells, while rendering or normalising the same file
costs seconds. So the gate reads text where text exists and only falls back to a picture of
page one for files with no text layer at all.

A view is capped on purpose: the gate must not pay for whole documents. `full_chars` and
`price_hints` are counted over the whole readable text, `view` is what we send.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ict.parse.census import FileEntry

PRICE_HINT = re.compile("单价|数量|总价|中标金额|成交金额|规格型号|品牌|制造商")
TABLE_HEAD_ROWS = 12


@dataclass
class Peek:
    file_id: str
    fmt: str
    readable: bool
    full_chars: int = 0
    price_hints: int = 0
    view: str = ""
    table_headers: list[list[str]] = field(default_factory=list)
    media_count: int = 0
    reason: str = ""

    def short(self) -> dict:
        return {"file_id": self.file_id, "format": self.fmt, "readable": self.readable,
                "full_chars": self.full_chars, "price_hints": self.price_hints,
                "view_chars": len(self.view), "headers": self.table_headers[:2], "reason": self.reason}


def _pdf_text(path, limit_pages: int = 40) -> tuple[str, list[list[str]]]:
    import pymupdf

    parts: list[str] = []
    headers: list[list[str]] = []
    document = pymupdf.open(str(path))
    try:
        for index in range(min(document.page_count, limit_pages)):
            page = document[index]
            parts.append(page.get_text() or "")
            if len(headers) < 3:
                try:
                    for table in page.find_tables().tables[:1]:
                        rows = table.extract()
                        if rows and rows[0]:
                            headers.append([str(cell or "").strip().replace("\n", "") for cell in rows[0]])
                except Exception:  # noqa: BLE001 - header discovery is opportunistic
                    pass
    finally:
        document.close()
    return "\n".join(parts), headers


def _docx_text(path) -> tuple[str, list[list[str]]]:
    import docx

    document = docx.Document(str(path))
    parts = [paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()]
    headers: list[list[str]] = []
    for table in document.tables[:3]:
        rows = [[cell.text.strip().replace("\n", " ") for cell in row.cells] for row in table.rows[:TABLE_HEAD_ROWS]]
        if rows:
            headers.append(rows[0])
        for row in rows[1:]:
            parts.append(" | ".join(row))
    return "\n".join(parts), headers


def _xlsx_text(path) -> tuple[str, list[list[str]]]:
    import openpyxl

    book = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    parts: list[str] = []
    headers: list[list[str]] = []
    try:
        for sheet in book.worksheets[:3]:
            for row_index, row in enumerate(sheet.iter_rows(values_only=True)):
                if row_index >= TABLE_HEAD_ROWS:
                    break
                cells = ["" if cell is None else str(cell).strip() for cell in row]
                if not any(cells):
                    continue
                if row_index == 0:
                    headers.append(cells)
                parts.append(" | ".join(cells))
    finally:
        book.close()
    return "\n".join(parts), headers


def docx_media(path) -> list[str]:
    """Pictures inside a .docx, in document order. A thin docx is often just scans."""
    import zipfile

    try:
        with zipfile.ZipFile(path) as archive:
            names = [name for name in archive.namelist() if name.startswith("word/media/")]
    except Exception:  # noqa: BLE001 - unreadable archive is not a media source
        return []
    rank = re.compile(r"(\d+)")

    def order(name: str) -> tuple:
        digits = rank.findall(name)
        return (int(digits[-1]) if digits else 10**9, name)

    return sorted(names, key=order)


def peek_entry(entry: FileEntry, view_chars: int = 3000) -> Peek:
    """Read as much as the gate needs. Never raises: unreadable becomes a flag."""
    peek = Peek(file_id=entry.file_id, fmt=entry.fmt, readable=False)
    if entry.fmt == "docx":
        peek.media_count = len(docx_media(entry.path))
    if entry.needs_normalisation:
        peek.reason = "needs_normalisation:" + (entry.fmt + (":" + entry.note if entry.note else ""))
        return peek
    loader = {"pdf": _pdf_text, "docx": _docx_text, "xlsx": _xlsx_text}.get(entry.fmt)
    if loader is None:
        peek.reason = "no_text_reader:" + entry.fmt
        return peek
    try:
        text, headers = loader(entry.path)
    except Exception as exc:  # noqa: BLE001 - a broken file must not stop the run
        peek.reason = "peek_failed:" + type(exc).__name__
        return peek
    peek.full_chars = len(text)
    peek.price_hints = len(PRICE_HINT.findall(text))
    peek.table_headers = headers
    peek.view = text[:view_chars]
    if entry.fmt == "pdf" and text.strip():
        density = peek.full_chars // max(1, entry.pages or 1)
        peek.readable = density >= 20
        if not peek.readable:
            peek.reason = "scan_pdf_density_%d" % density
    else:
        peek.readable = peek.full_chars >= 200
        if not peek.readable:
            peek.reason = "thin_text_%d" % peek.full_chars
    return peek
