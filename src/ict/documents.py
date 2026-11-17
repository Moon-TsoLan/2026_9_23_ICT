"""Native readers for formats the application machine can read without a model.

PDFs no longer live here: they are parsed page-run by the parsing service, so nothing in
this module can resurrect the old `attachments-md` tree or its page-range guessing.
"""

from __future__ import annotations

from pathlib import Path

from ict.concurrency import LOCAL_GATE


def read_docx(path: Path) -> list[dict]:
    import docx
    import base64
    from docx.oxml.ns import qn
    from docx.text.paragraph import Paragraph
    from docx.table import Table

    document = docx.Document(str(path))
    parts, tables, blocks = [], [], []
    for element in document.element.body:
        if element.tag == qn("w:p"):
            text = Paragraph(element, document).text
            parts.append(text)
            blocks.append({"text": text, "tables": []})
        elif element.tag == qn("w:tbl"):
            rows = [[cell.text.strip() for cell in row.cells] for row in Table(element, document).rows]
            table = {"table_index": len(tables), "headers": rows[0] if rows else [], "rows": rows[1:]}
            tables.append(table)
            parts.extend(" | ".join(row) for row in rows)
            blocks.append({"text": "", "tables": [table]})
        else:
            continue
        images = []
        for image in element.iter(qn("a:blip")):
            part = document.part.related_parts[image.get(qn("r:embed"))]
            images.append("data:%s;base64,%s" % (part.content_type, base64.b64encode(part.blob).decode("ascii")))
        if images:
            blocks[-1]["images"] = images
    text = "\n".join(parts)
    return [{"page_no": 1, "text": text, "tables": tables, "blocks": blocks,
             "chars": len(text), "source": "docx", "coordinate_kind": "native_segment"}]


def read_xlsx(path: Path) -> list[dict]:
    import openpyxl

    book = openpyxl.load_workbook(path, read_only=True, data_only=True)
    pages = []
    for index, sheet in enumerate(book.worksheets, start=1):
        rows = [["" if cell is None else str(cell) for cell in row]
                for row in sheet.iter_rows(values_only=True)]
        rows = [row for row in rows if any(row)]
        text = "\n".join("\t".join(row) for row in rows)
        pages.append({"page_no": index, "text": text,
                      "tables": [{"table_index": 0, "headers": rows[0] if rows else [], "rows": rows[1:]}],
                      "chars": len(text), "source": "xlsx", "sheet_name": sheet.title,
                      "coordinate_kind": "worksheet"})
    book.close()
    return pages


def read_native(path: Path, fmt: str | None = None) -> list[dict]:
    """DOCX, spreadsheets and text use their sniffed container; no rasterising or model."""
    suffix = "." + fmt if fmt else path.suffix.lower()
    # Reading a whole spreadsheet or tender document is CPU work, so it queues behind the same
    # gate as the other native readers.
    with LOCAL_GATE:
        if suffix == ".docx":
            return read_docx(path)
        if suffix in {".xlsx", ".xlsm"}:
            # A stream prevents suffix checks from contradicting the sniffed container.
            with path.open("rb") as stream:
                return read_xlsx(stream)
        if suffix == ".text":
            text = read_text(path)
            return [{"page_no": 1, "text": text, "tables": [], "chars": len(text), "source": "text"}]
    raise ValueError("unsupported native document " + suffix)


def read_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeError:
            continue
    return raw.decode("utf-8", errors="replace")
