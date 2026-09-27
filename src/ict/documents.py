"""Read text and tables from already extracted attachment files.

PDF files are read from work/attachments-md when a markdown twin exists.
The twin keeps the attachment tree and uses a .md suffix. A later PDF-to-markdown
model should write into that same tree.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from pathlib import Path

from bs4 import BeautifulSoup

from ict.config import ATTACHMENTS_MD_ROOT, ATTACHMENTS_ROOT

PAGE_MARK_RE = re.compile(r"<!--\s*pages?\s+(\d+)\s*(?:-\s*(\d+))?\s*-->", re.IGNORECASE)


class MarkdownUnavailable(RuntimeError):
    """Raised when a PDF has no markdown twin and Paddle cannot produce one."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def markdown_path_for_pdf(pdf_path: Path) -> Path | None:
    try:
        relative = pdf_path.resolve().relative_to(ATTACHMENTS_ROOT.resolve())
    except ValueError:
        return None
    candidate = ATTACHMENTS_MD_ROOT / relative.with_suffix(".md")
    return candidate if candidate.is_file() else None


def convert_with_paddle(pdf_path: Path) -> Path | None:
    """Run the optional local Paddle command. None means Paddle is not connected."""
    command = os.environ.get("ICT_PADDLE_COMMAND", "").strip()
    if not command:
        return None
    relative = pdf_path.resolve().relative_to(ATTACHMENTS_ROOT.resolve())
    target = ATTACHMENTS_MD_ROOT / relative.with_suffix(".md")
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([*shlex.split(command, posix=False), str(pdf_path), str(target)], check=True)
    return target if target.is_file() else None


def ensure_markdown(pdf_path: Path) -> Path:
    found = markdown_path_for_pdf(pdf_path)
    if found is not None:
        return found
    try:
        produced = convert_with_paddle(pdf_path)
    except Exception as exc:
        raise MarkdownUnavailable("conversion_failed") from exc
    if produced is None:
        raise MarkdownUnavailable("paddle_not_connected")
    return produced


def _tables_and_text(fragment: str) -> tuple[str, list[dict]]:
    soup = BeautifulSoup(fragment, "html.parser")
    tables = []
    for index, table in enumerate(soup.find_all("table")):
        rows = [
            [cell.get_text(" ", strip=True) for cell in row.find_all(["td", "th"])]
            for row in table.find_all("tr")
        ]
        rows = [row for row in rows if any(row)]
        headers = rows[0] if rows else []
        tables.append({"table_index": index, "headers": headers, "rows": rows[1:]})
        table.decompose()
    text = soup.get_text("\n", strip=True)
    return text, tables


def _markdown_segments(raw: str) -> list[tuple[int, int, str]]:
    marks = list(PAGE_MARK_RE.finditer(raw))
    if not marks:
        pieces = [piece.strip() for piece in re.split(r"<break\s*/?>", raw, flags=re.IGNORECASE) if piece.strip()]
        return [(index, index, piece) for index, piece in enumerate(pieces, start=1)] or [(1, 1, raw)]
    segments: list[tuple[int, int, str]] = []
    for index, mark in enumerate(marks):
        start = int(mark.group(1))
        end = int(mark.group(2) or start)
        chunk_end = marks[index + 1].start() if index + 1 < len(marks) else len(raw)
        segments.append((start, end, raw[mark.end() : chunk_end].strip()))
    return segments


def read_markdown_pages(path: Path, page_nos: list[int] | None = None) -> list[dict]:
    segments = _markdown_segments(path.read_text(encoding="utf-8"))
    selected = set(page_nos or [])
    pages = []
    for start, end, fragment in segments:
        if selected and not any(start <= number <= end for number in selected):
            continue
        text, tables = _tables_and_text(fragment)
        page_no = start
        if selected:
            page_no = next(number for number in sorted(selected) if start <= number <= end)
        pages.append({"page_no": page_no, "text": text, "tables": tables, "chars": len(text), "source": "markdown"})
    return pages


def read_pdf_pages(path: Path, page_nos: list[int] | None = None) -> list[dict]:
    return read_markdown_pages(ensure_markdown(path), page_nos)


def read_docx(path: Path) -> list[dict]:
    import docx

    document = docx.Document(str(path))
    text = "\n".join(paragraph.text for paragraph in document.paragraphs if paragraph.text.strip())
    tables = []
    for index, table in enumerate(document.tables):
        rows = [[cell.text.strip() for cell in row.cells] for row in table.rows]
        headers = rows[0] if rows else []
        tables.append({"table_index": index, "headers": headers, "rows": rows[1:]})
    return [{"page_no": 1, "text": text, "tables": tables, "chars": len(text)}]


def read_xlsx(path: Path) -> list[dict]:
    import openpyxl

    book = openpyxl.load_workbook(path, read_only=True, data_only=True)
    pages = []
    for index, sheet in enumerate(book.worksheets, start=1):
        rows = []
        for row in sheet.iter_rows(values_only=True):
            rows.append(["" if cell is None else str(cell) for cell in row])
        headers = rows[0] if rows else []
        text = "\n".join("\t".join(row) for row in rows[:20])
        pages.append(
            {
                "page_no": index,
                "text": text,
                "tables": [{"table_index": 0, "headers": headers, "rows": rows[1:]}],
                "chars": len(text),
            }
        )
    book.close()
    return pages


def read_document(path: Path, page_nos: list[int] | None = None) -> list[dict]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return read_pdf_pages(path, page_nos)
    if suffix == ".docx":
        return read_docx(path)
    if suffix in {".xlsx", ".xlsm"}:
        return read_xlsx(path)
    raise ValueError(f"unsupported document {suffix}")
