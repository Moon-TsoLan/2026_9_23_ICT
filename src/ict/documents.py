"""Read text and tables from already extracted attachment files."""

from __future__ import annotations

from pathlib import Path


def read_pdf_pages(path: Path, page_nos: list[int] | None = None) -> list[dict]:
    import pymupdf

    document = pymupdf.open(path)
    selected = page_nos or list(range(1, document.page_count + 1))
    pages = []
    for number in selected:
        if number < 1 or number > document.page_count:
            continue
        page = document[number - 1]
        text = page.get_text() or ""
        tables = []
        finder = getattr(page, "find_tables", None)
        if finder:
            try:
                found = finder()
                for index, table in enumerate(getattr(found, "tables", []) or []):
                    rows = table.extract() or []
                    headers = [str(cell or "") for cell in (rows[0] if rows else [])]
                    body = [[str(cell or "") for cell in row] for row in rows[1:]]
                    tables.append({"table_index": index, "headers": headers, "rows": body})
            except Exception:
                tables = []
        pages.append({"page_no": number, "text": text, "tables": tables, "chars": len(text)})
    document.close()
    return pages


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
