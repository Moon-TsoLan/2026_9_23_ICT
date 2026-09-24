"""Turn a ccgp HTML notice into the minimal contexts required by steps 1 and 2."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from bs4 import BeautifulSoup, Tag

from ict.money import parse_amount

PACKAGE_RE = re.compile(r"(?:采购包|合同包|第)\s*([0-9]+|[A-Za-z]+)\s*包?")
PACKAGE_LIST_RE = re.compile(
    r"(?:采购包|合同包)\s*([0-9A-Za-z]+(?:\s*[、,，]\s*[0-9A-Za-z]+)+)"
)
PROJECT_NO_RE = re.compile(r"项目编号[:：]\s*([A-Za-z0-9\-]+)")
AMOUNT_RE = re.compile(r"[￥¥]?\s*[\d,，]+(?:\.\d+)?\s*(?:万元|元)")


def _text(node) -> str:
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()


@dataclass
class ParsedTable:
    table_index: int
    before_text: str
    headers: list[str]
    rows: list[list[str]]
    section: str


@dataclass
class ParsedNotice:
    announcement_id: str
    title: str
    summary: dict[str, str]
    headings: list[str]
    tables: list[ParsedTable]
    package_hints: list[dict]
    source_project_no: str | None = None
    body_packages: list[str] = field(default_factory=list)


def _summary(soup: BeautifulSoup) -> dict[str, str]:
    box = soup.select_one("div.table")
    table = box.find("table") if box else None
    if table is None:
        return {}
    values: dict[str, str] = {}
    for row in table.find_all("tr"):
        cells = row.find_all("td")
        index = 0
        while index < len(cells):
            cell = cells[index]
            if "title" in (cell.get("class") or []):
                key = _text(cell)
                if index + 1 < len(cells):
                    values[key] = _text(cells[index + 1])
                    index += 2
                    continue
            index += 1
    return values


def _headers_and_rows(table: Tag) -> tuple[list[str], list[list[str]]]:
    header_cells = table.find_all("th")
    body_rows = table.find_all("tr")
    if header_cells:
        headers = [_text(cell) for cell in header_cells]
        data_rows = []
        for row in body_rows:
            cells = row.find_all("td")
            if cells:
                data_rows.append([_text(cell) for cell in cells])
        return headers, data_rows
    rows = [[_text(cell) for cell in row.find_all(["td", "th"])] for row in body_rows]
    rows = [row for row in rows if any(row)]
    if not rows:
        return [], []
    width = max(len(row) for row in rows)
    headers = rows[0]
    if len(headers) < width:
        headers = headers + [""] * (width - len(headers))
    return headers, rows[1:]


def _nearby_amount(text: str) -> dict | None:
    match = AMOUNT_RE.search(text)
    if not match:
        return None
    raw = match.group()
    yuan, _ = parse_amount(raw)
    return {"raw_text": raw, "amount_yuan": yuan, "scope": "package", "confidence": 0.7}


def parse_notice(path: Path) -> ParsedNotice:
    announcement_id = path.stem
    soup = BeautifulSoup(path.read_text(encoding="utf-8", errors="replace"), "lxml")
    for tag in soup.select("script, style"):
        tag.decompose()
    title_node = soup.select_one("h2.tc")
    meta = soup.find("meta", attrs={"name": "ArticleTitle"})
    title = _text(title_node) if title_node else (meta.get("content", "") if meta else "")
    summary = _summary(soup)
    content = soup.select_one("div.vF_detail_content") or soup
    headings: list[str] = []
    tables: list[ParsedTable] = []
    hints: list[dict] = []
    before = ""
    section = ""
    for node in content.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "table"]):
        if node.name == "table":
            if node.find_parent("div", class_="table"):
                continue
            headers, rows = _headers_and_rows(node)
            tables.append(
                ParsedTable(len(tables), before[-180:], headers, rows, section)
            )
            continue
        text = _text(node)
        if not text:
            continue
        if node.name.startswith("h"):
            headings.append(text)
            section = text
        before = text
        location = "html_heading" if node.name.startswith("h") else "html_text"
        listed = False
        for match in PACKAGE_LIST_RE.finditer(text):
            listed = True
            for package_no in re.split(r"\s*[、,，]\s*", match.group(1)):
                hints.append(
                    {
                        "text": f"采购包{package_no}",
                        "location": location,
                        "package_evidence_text": text[:240],
                        "package_no": package_no,
                        "package_amount": _nearby_amount(text),
                    }
                )
        if not listed:
            for match in PACKAGE_RE.finditer(text):
                if match.group(0).startswith("第") and not match.group(0).endswith("包"):
                    continue
                hints.append(
                    {
                        "text": match.group(0),
                        "location": location,
                        "package_evidence_text": text[:240],
                        "package_no": match.group(1),
                        "package_amount": _nearby_amount(text),
                    }
                )
    body = _text(content)
    project_no = PROJECT_NO_RE.search(body)
    seen: list[str] = []
    for hint in hints:
        if hint["package_no"] not in seen:
            seen.append(hint["package_no"])
    return ParsedNotice(
        announcement_id=announcement_id,
        title=title,
        summary=summary,
        headings=headings,
        tables=tables,
        package_hints=hints,
        source_project_no=project_no.group(1) if project_no else None,
        body_packages=seen,
    )


def step1_payload(notice: ParsedNotice) -> dict:
    summary_keys = ("采购项目名称", "品目", "采购单位", "总中标金额", "总成交金额")
    summary = {key: notice.summary[key] for key in summary_keys if key in notice.summary}
    return {
        "announcement_title": notice.title,
        "summary_table": summary,
        "html_headings": notice.headings[:40],
        "table_headers": [table.headers for table in notice.tables[:30]],
        "package_hints": notice.package_hints[:40],
        "source_project_no_hint": notice.source_project_no,
    }


def step2a_payload(table: ParsedTable, package_candidates: list[str]) -> dict:
    return {
        "table_index": table.table_index,
        "before_text": table.before_text,
        "headers": table.headers,
        "first_rows": table.rows[:3],
        "package_candidates": package_candidates,
    }
