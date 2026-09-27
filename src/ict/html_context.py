"""Turn a ccgp HTML notice into the minimal contexts required by steps 1 and 2."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from bs4 import BeautifulSoup, Tag

from ict.money import parse_amount

PACKAGE_RE = re.compile(r"(?:采购包|合同包|标\s*包|第)\s*[:：]?\s*([0-9]+|[A-Za-z]+)\s*包?")
PACKAGE_LIST_RE = re.compile(
    r"(?:采购包|合同包|标\s*包)\s*[:：]?\s*([0-9A-Za-z]+(?:\s*[、,，]\s*[0-9A-Za-z]+)+)"
)
KV_LABEL_RE = re.compile(r"标\s*包|供应商|金额|名称|服务范围|服务标准|服务时间|服务要求|地址|品牌|规格|数量|单价|施工范围")
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
    key_value: bool = False


@dataclass
class ParsedNotice:
    announcement_id: str
    title: str
    summary: dict[str, str]
    headings: list[str]
    tables: list[ParsedTable]
    package_hints: list[dict]
    sections: list[dict] = field(default_factory=list)
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
    if _looks_like_kv(rows):
        pairs = [(row + [""])[:2] for row in rows]
        return ["字段", "内容"], pairs
    width = max(len(row) for row in rows)
    headers = rows[0]
    if len(headers) < width:
        headers = headers + [""] * (width - len(headers))
    return headers, rows[1:]


def _looks_like_kv(rows: list[list[str]]) -> bool:
    if len(rows) < 2 or any(len(row) > 2 for row in rows):
        return False
    hits = sum(1 for row in rows if row and KV_LABEL_RE.search(row[0]))
    return hits >= 2


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
    sections: list[dict] = []
    before = ""
    section = ""
    section_parts: list[str] = []
    for node in content.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "table", "div"]):
        if node.name == "div":
            classes = " ".join(node.get("class") or [])
            if not any(token in classes for token in ("second-title", "title-box")):
                continue
            if node.find("table"):
                continue
        if node.name == "table":
            if node.find_parent("div", class_="table"):
                continue
            headers, rows = _headers_and_rows(node)
            key_value = headers[:1] == ["字段"]
            tables.append(
                ParsedTable(len(tables), before[-240:], headers, rows, section, key_value)
            )
            for row in rows:
                _collect_package_hints(hints, " ".join(row), "html_table")
            continue
        text = _text(node)
        if not text:
            continue
        if node.name.startswith("h") or node.name == "div":
            if section and section_parts:
                sections.append({"title": section, "text": " ".join(section_parts)[:1600]})
            headings.append(text)
            section = text
            section_parts = []
        else:
            section_parts.append(text[:500])
        before = text
        location = "html_heading" if node.name != "p" else "html_text"
        _collect_package_hints(hints, text, location)
    if section and section_parts:
        sections.append({"title": section, "text": " ".join(section_parts)[:1600]})
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
        sections=sections,
        source_project_no=project_no.group(1) if project_no else None,
        body_packages=seen,
    )


def _collect_package_hints(hints: list[dict], text: str, location: str) -> None:
    listed = False
    for match in PACKAGE_LIST_RE.finditer(text):
        listed = True
        for package_no in re.split(r"\s*[、,，]\s*", match.group(1)):
            hints.append(_hint(package_no, text, location))
    if listed:
        return
    for match in PACKAGE_RE.finditer(text):
        if match.group(0).startswith("第") and not match.group(0).endswith("包"):
            continue
        hints.append(_hint(match.group(1), text, location))


def _hint(package_no: str, text: str, location: str) -> dict:
    return {
        "text": f"包{package_no}",
        "location": location,
        "package_evidence_text": text[:400],
        "package_no": package_no,
        "package_amount": _nearby_amount(text),
    }


def _table_preview(table: ParsedTable) -> dict:
    limit = 20 if table.key_value else 8
    return {
        "table_index": table.table_index,
        "before_text": table.before_text,
        "section": table.section,
        "key_value": table.key_value,
        "headers": table.headers,
        "rows": table.rows[:limit],
    }


def step1_payload(notice: ParsedNotice) -> dict:
    summary_keys = ("采购项目名称", "品目", "采购单位", "总中标金额", "总成交金额", "评审专家名单")
    summary = {key: notice.summary[key] for key in summary_keys if key in notice.summary}
    useful_sections = [
        section
        for section in notice.sections
        if re.search(r"中标|成交|主要标的|分包", section["title"])
    ]
    return {
        "announcement_title": notice.title,
        "summary_table": summary,
        "html_headings": notice.headings[:40],
        "body_sections": useful_sections[:8],
        "tables": [_table_preview(table) for table in notice.tables[:12]],
        "package_hints": notice.package_hints[:40],
        "source_project_no_hint": notice.source_project_no,
    }


def bidder_body_sections(notice: ParsedNotice) -> list[dict]:
    """Bidder prose that never becomes a table, so step 2B would otherwise not see it."""
    found = []
    for section in notice.sections:
        title = section["title"]
        text = section["text"]
        score_list = (
            "评审专家" in title
            and re.search(r"[（(]\s*\d", text)
            and re.search(r"公司|供应商", text)
        )
        lost_bid = "未中标" in title and re.search(r"公司|供应商", text)
        if score_list or lost_bid:
            found.append({"title": title, "text": text})
    return found


def step2a_payload(table: ParsedTable, package_candidates: list[str]) -> dict:
    return {
        "table_index": table.table_index,
        "before_text": table.before_text,
        "headers": table.headers,
        "first_rows": table.rows[:12],
        "package_candidates": package_candidates,
    }
