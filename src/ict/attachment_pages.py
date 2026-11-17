"""Local structural reads and bounded, lossless source contexts."""
from __future__ import annotations
import base64
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory

from ict.config import PAGE_CONTEXT_CHARS, SCREEN_THUMB_DPI
from ict.concurrency import LOCAL_GATE
from ict.documents import read_native


def office_pdf(path, directory):
    """Convert legacy Office locally, without invoking OCR."""
    executable = shutil.which("soffice") or str(Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) /
                                               "LibreOffice/program/soffice.exe")
    profile = (Path(directory) / "profile").as_uri()
    with LOCAL_GATE:
        result = subprocess.run([executable, "-env:UserInstallation=" + profile, "--headless", "--convert-to", "pdf",
                                 "--outdir", str(directory), str(path)], capture_output=True, timeout=120,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    output = Path(directory) / (Path(path).stem + ".pdf")
    if result.returncode or not output.is_file():
        raise ValueError("office_convert_failed:" + result.stderr.decode(errors="replace")[:200])
    return output


def pdf_pages(path, page_nos):
    import pymupdf
    with pymupdf.open(path) as document:
        for number in page_nos:
            with LOCAL_GATE:
                page = document[number - 1]
                text = page.get_text()
                tables = []
                for index, table in enumerate(page.find_tables().tables):
                    rows = [[str(cell or "") for cell in row] for row in table.extract()]
                    tables.append({"table_index": index, "headers": rows[0] if rows else [], "rows": rows[1:]})
                images = []
                # Presence of an image is structural, not a judgment of usefulness/readability.
                if page.get_images() or not text.strip():
                    pixmap = page.get_pixmap(dpi=SCREEN_THUMB_DPI)
                    images = ["data:image/png;base64," + base64.b64encode(pixmap.tobytes("png")).decode("ascii")]
            yield {"page_no": number, "text": text, "tables": tables, "images": images,
                   "source_type": "pdf", "coordinate_kind": "pdf_page"}


def source_pages(entry, choose_pdf_pages, stack):
    """Yield original PDF pages or native logical pages; never invent Word physical pages."""
    if entry.fmt == "pdf":
        import pymupdf
        with pymupdf.open(entry.path) as document:
            entry.pages = document.page_count
        yield from pdf_pages(entry.path, choose_pdf_pages(entry))
    elif entry.fmt in {"ole", "doc", "xls", "rtf"} or (entry.fmt == "docx" and _has_docx_images(entry.path)):
        directory = stack.enter_context(TemporaryDirectory(prefix="ict-office-"))
        converted = office_pdf(entry.path, directory)
        from dataclasses import replace
        import pymupdf
        with pymupdf.open(converted) as document:
            pages = document.page_count
        proxy = replace(entry, path=converted, fmt="pdf", pages=pages)
        for page in pdf_pages(converted, choose_pdf_pages(proxy)):
            yield {**page, "parse_path": converted, "coordinate_kind": "rendered_office_page"}
    elif entry.fmt in {"docx", "xlsx", "xlsm", "text"}:
        for page in read_native(entry.path, entry.fmt):
            yield {**page, "source_type": "xlsx" if entry.fmt == "xlsm" else entry.fmt,
                   "coordinate_kind": page.get("coordinate_kind", "native_segment")}
    elif entry.fmt == "image":
        from PIL import Image
        from io import BytesIO
        with Image.open(entry.path) as document:
            entry.pages = getattr(document, "n_frames", 1)
            for number in choose_pdf_pages(entry):
                index = number - 1
                document.seek(index)
                buffer = BytesIO()
                document.convert("RGB").save(buffer, format="PNG")
                yield {"page_no": index + 1, "text": "", "tables": [], "source_type": "pdf",
                       "coordinate_kind": "image_frame", "images": ["data:image/png;base64," +
                        base64.b64encode(buffer.getvalue()).decode("ascii")]}
    else:
        raise ValueError("unsupported_container:" + entry.fmt)


def _has_docx_images(path):
    from ict.parse.peek import docx_media
    return bool(docx_media(path))


def split_page(file_id, file_name, page):
    """Chunk all text and every table row without truncating later content."""
    segments = []
    root = "file:%s:p%s" % (file_id, page["page_no"])
    blocks = page.get("blocks") or [{"text": page.get("text", ""), "tables": page.get("tables", []),
                                      "images": page.get("images", [])}]
    images = []
    for block_index, block in enumerate(blocks):
        images.extend(block.get("images", []))
        if block.get("text"):
            segments.append({"source_ref": root + ":b%d" % block_index, "text": block["text"]})
        for table in block.get("tables", []):
            for index, cells in enumerate(table["rows"]):
                segments.append({"source_ref": root + ":t%d:r%d" % (table["table_index"], index),
                    "table_index": table["table_index"], "row_index": index,
                    "headers": table["headers"], "text": " | ".join(cells)})
    bounded = []
    for segment in segments:
        text = segment["text"]
        for offset in range(0, len(text), PAGE_CONTEXT_CHARS):
            bounded.append({**segment, "text": text[offset:offset + PAGE_CONTEXT_CHARS],
                            "source_ref": segment["source_ref"] + ":part%d" % (offset // PAGE_CONTEXT_CHARS)})
    if not bounded:
        bounded = [{"source_ref": root, "text": ""}]
    chunks, current, size = [], [], 0
    for segment in bounded:
        if current and size + len(segment["text"]) > PAGE_CONTEXT_CHARS:
            chunks.append(current)
            current, size = [], 0
        current.append(segment)
        size += len(segment["text"])
    chunks.append(current)
    return [{"file_id": file_id, "file_name": file_name, "page_no": page["page_no"],
             "source_type": page.get("source_type", "pdf"), "coordinate_kind": page.get("coordinate_kind", "native_segment"),
             "source_segments": [{**segment, "file_id": file_id, "page_no": page["page_no"]} for segment in chunk],
             "images": images if index == 0 else []} for index, chunk in enumerate(chunks)]
