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
# 项目编号的原文抽取。只作为第 1 步的线索（source_project_no_hint）送给模型，
# 不做任何判定。真实公告有三种写法，旧式 `项目编号[:：]([A-Za-z0-9-]+)` 只认第一种的一部分：
#   1. 值写在标题里：`一、项目编号：440001-2026-27404`
#   2. 标签带括号说明：`项目编号（政府采购计划编号）：SDGP...`、`项目编号（或招标编号…，如有）：...`
#   3. 标题只有标签、值在下一个节点：正文里成了 `一、项目编号 SDGP370203000202602000053`
# 值本身还会带 `/`、`[]`、`（）`、中文（`NJC/A260034`、`[231201]ALAZ[CS]20260001`、
# `KSHZX（GK）2026-002`、`FS34000120264452号`、`豫财招标采购-2025-1596`），
# 所以不能再按字符白名单截断。
_PROJECT_NO_STOP = (
    r"(?![一二三四五六七八九十]、|\d+、|项目名称|采购人|采购单位|采购方式|中标|成交|供应商)"
)
PROJECT_NO_RE = re.compile(
    r"项目\s*编号"
    r"(?:\s*[（(][^）)]{0,80}[）)])?"          # 可选的括号说明，如“（政府采购计划编号）”
    r"\s*[：:]?\s*"                              # 冒号可写可不写
    r"([^\s，。；、）)](?:" + _PROJECT_NO_STOP + r"[^\s，。；、]){0,59})"
)


def _clean_project_no(value: str) -> str | None:
    """Strip stray trailing closers; a value like 'TGPC-2026-D-0028)' must not keep the ')'."""
    value = value.strip().strip("，。；、")
    opens = value.count("（") + value.count("(")
    closes = value.count("）") + value.count(")")
    while value and value[-1] in "）)" and closes > opens:
        value = value[:-1]
        closes -= 1
    value = value.strip()
    # 项目编号一定含数字或字母；纯中文的（如“无”“详见附件”）不是编号，宁可不给。
    if not value or not any(ch.isdigit() or ch.isascii() and ch.isalpha() for ch in value):
        return None
    return value
AMOUNT_RE = re.compile(r"[￥¥]?\s*[\d,，]+(?:\.\d+)?\s*[（(]?\s*(?:万元|元)")
NUMBERED_SPLIT = re.compile(r"(?=(?:^|\s)[一二三四五六七八九十百]+、)")
COMPANY_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9（）()]{2,40}?(?:有限公司|股份公司|公司)")


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
    package_anchors: list[dict] = field(default_factory=list)


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
    rows = [_split_embedded_label(row) for row in rows]
    if _looks_like_kv(rows):
        pairs = [(row + [""])[:2] for row in rows]
        return ["字段", "内容"], pairs
    width = max(len(row) for row in rows)
    headers = rows[0]
    if len(headers) < width:
        headers = headers + [""] * (width - len(headers))
    return headers, rows[1:]


def _split_embedded_label(row: list[str]) -> list[str]:
    """Turn '名称：某某' in the only cell, or beside an empty cell, into key and value."""
    if not row or not row[0] or not re.search(r"[:：]", row[0]):
        return row
    if len(row) >= 2 and row[1]:
        return row
    key, value = re.split(r"[:：]", row[0], maxsplit=1)
    if not key.strip() or not value.strip():
        return row
    return [key.strip(), value.strip(), *row[2:]]


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
        pieces = [piece.strip() for piece in NUMBERED_SPLIT.split(text) if piece.strip()]
        for piece in pieces:
            numbered = re.match(r"[一二三四五六七八九十百]+、", piece)
            if node.name.startswith("h") or node.name == "div" or numbered:
                if section and section_parts:
                    sections.append({"title": section, "text": " ".join(section_parts)[:4000]})
                headings.append(piece[:80])
                section = piece[:80]
                section_parts = [] if numbered and len(piece) <= 80 else ([piece] if numbered else [])
                if numbered and len(piece) > 80:
                    section_parts = [piece]
            else:
                section_parts.append(piece[:2000])
        before = text
        location = "html_heading" if node.name != "p" else "html_text"
        _collect_package_hints(hints, text, location)
    if section and section_parts:
        sections.append({"title": section, "text": " ".join(section_parts)[:4000]})
    body = _text(content)
    project_no = PROJECT_NO_RE.search(body)
    source_project_no = _clean_project_no(project_no.group(1)) if project_no else None
    seen: list[str] = []
    for hint in hints:
        if hint["package_no"] not in seen:
            seen.append(hint["package_no"])
    anchors = _package_anchors(sections)
    return ParsedNotice(
        announcement_id=announcement_id,
        title=title,
        summary=summary,
        headings=headings,
        tables=tables,
        package_hints=hints,
        sections=sections,
        source_project_no=source_project_no,
        body_packages=seen,
        package_anchors=anchors,
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


def _package_anchors(sections: list[dict]) -> list[dict]:
    """Package, supplier, and amount blocks from numbered body text."""
    anchors: list[dict] = []
    for section in sections:
        parts = re.split(r"(?=供应商名称\s*[:：])", section["text"])
        for part in parts:
            if "供应商名称" not in part and "金额" not in part:
                continue
            package = re.search(r"第\s*([0-9]+|[A-Za-z]+)\s*包", part)
            amount = AMOUNT_RE.search(part)
            if package is None and amount is None:
                continue
            companies = COMPANY_RE.findall(part)
            supplier = companies[-1] if companies else None
            yuan = None
            raw = None
            if amount:
                raw = amount.group(0)
                yuan, _ = parse_amount(raw)
            if package is None and supplier is None:
                continue
            anchors.append(
                {
                    "package_no": None if package is None else package.group(1),
                    "supplier_name": supplier,
                    "amount_raw": raw,
                    "amount_yuan": yuan,
                    "section_title": section["title"],
                    "text": part[:400],
                }
            )
    return anchors


def winner_hints(notice: ParsedNotice, package_nos: list[str]) -> dict[str, list[str]]:
    """Suppliers named in the award prose, by package. Losing-bid prose is excluded."""
    hints: dict[str, list[str]] = {}
    for anchor in notice.package_anchors:
        title = anchor.get("section_title") or ""
        if re.search(r"未中标|未成交", title) or not re.search(r"中标|成交", title):
            continue
        name = anchor.get("supplier_name")
        package_no = anchor.get("package_no")
        if package_no is None and len(package_nos) == 1:
            package_no = package_nos[0]
        if name and package_no in package_nos and name not in hints.get(package_no, []):
            hints.setdefault(package_no, []).append(name)
    return hints


def bidder_body_sections(notice: ParsedNotice) -> list[dict]:
    """Bidder prose selected by content, not by the heading the DOM happened to use."""
    found = []
    seen: set[str] = set()
    for section in notice.sections:
        blob = f"{section['title']} {section['text']}"
        company_score = ("公司" in blob or "供应商" in blob) and re.search(r"[（(]\s*\d", blob)
        supplier_amount = "供应商名称" in blob and ("金额" in blob or "万元" in blob)
        if not company_score and not supplier_amount:
            continue
        key = section["text"][:80]
        if key in seen:
            continue
        seen.add(key)
        found.append({"title": section["title"], "text": section["text"]})
    return found


def fill_package_amounts(understanding, notice: ParsedNotice) -> None:
    from ict.schemas import Amount

    found: dict[str, dict] = {}
    for anchor in notice.package_anchors:
        package_no = anchor.get("package_no")
        if package_no and anchor.get("amount_yuan") is not None:
            found.setdefault(package_no, anchor)
    for package in understanding.packages:
        current = package.package_amount
        if current is not None and current.amount_yuan is not None:
            continue
        anchor = found.get(package.package_no)
        if anchor is None:
            continue
        package.package_amount = Amount(
            raw_text=anchor.get("amount_raw"),
            amount_yuan=anchor.get("amount_yuan"),
            scope="package",
            confidence=0.8,
        )


def step2a_payload(table: ParsedTable, package_candidates: list[str], siblings: list[dict] | None = None) -> dict:
    return {
        "table_index": table.table_index,
        "before_text": table.before_text,
        "headers": table.headers,
        "first_rows": table.rows[:12],
        "section": table.section,
        "key_value": table.key_value,
        "package_candidates": package_candidates,
        "other_tables": siblings or [],
    }
