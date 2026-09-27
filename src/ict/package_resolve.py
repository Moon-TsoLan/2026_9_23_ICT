"""Deterministic package assignment after each table has been read on its own."""

from __future__ import annotations

import json
import re
from collections import Counter

from ict.html_context import COMPANY_RE, ParsedNotice
from ict.llm import LLMClient, complete_json
from ict.schemas import HtmlTable, HtmlTables

ITEM_NO_RE = re.compile(r"^\s*([0-9]+|[A-Za-z]+)\s*[-－]")
OBJECT_KEYS = ("名称", "服务范围", "施工范围", "标的", "货物名称", "采购标的")


def promote_object_tables(notice: ParsedNotice, tables: HtmlTables) -> None:
    """A table in the object section is still extracted when the model says other."""
    by_index = {table.table_index: table for table in notice.tables}
    for understood in tables.tables:
        if understood.table_role != "other":
            continue
        parsed = by_index.get(understood.table_index)
        if parsed is None:
            continue
        blob = " ".join([parsed.section, parsed.before_text, *parsed.headers, *(cell for row in parsed.rows[:6] for cell in row)])
        if "主要标的" in parsed.section or any(key in blob for key in OBJECT_KEYS):
            understood.table_role = "cob_detail"
            understood.row_grain = "cob"
            if "promoted_from_other" not in understood.issues:
                understood.issues.append("promoted_from_other")


def resolve_table_packages(
    notice: ParsedNotice,
    tables: HtmlTables,
    package_nos: list[str],
    llm: LLMClient | None = None,
    counter: Counter | None = None,
) -> None:
    known = set(package_nos)
    suppliers = _supplier_packages(notice, tables, known)
    by_index = {table.table_index: table for table in notice.tables}
    for understood in tables.tables:
        if understood.package_scope in known:
            continue
        parsed = by_index.get(understood.table_index)
        if parsed is None:
            continue
        matched = _match_supplier(parsed, suppliers)
        if matched:
            understood.package_scope = matched
            continue
        item_package = _item_number_package(parsed, known)
        if item_package:
            understood.package_scope = item_package
    _assign_isomorphic(notice, tables, package_nos)
    pending = [
        item
        for item in tables.tables
        if item.table_role in {"cob_detail", "cob_summary", "sub_score", "winner"}
        and item.package_scope not in known
        and item.package_scope != "announcement"
    ]
    if pending and llm is not None and package_nos:
        _ask_model(notice, pending, package_nos, llm, counter)


def _supplier_packages(notice: ParsedNotice, tables: HtmlTables, known: set[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for anchor in notice.package_anchors:
        name = anchor.get("supplier_name")
        package_no = anchor.get("package_no")
        if name and package_no in known:
            mapping[name] = package_no
    parsed_by_index = {table.table_index: table for table in notice.tables}
    for understood in tables.tables:
        if understood.table_role != "winner" or understood.package_scope not in known:
            continue
        parsed = parsed_by_index.get(understood.table_index)
        if parsed is None:
            continue
        for row in parsed.rows:
            for cell in row:
                for name in COMPANY_RE.findall(cell):
                    mapping[name] = understood.package_scope
    return mapping


def _match_supplier(parsed, suppliers: dict[str, str]) -> str | None:
    blob = f"{parsed.before_text} {parsed.section}"
    hits = [package for name, package in suppliers.items() if name and name in blob]
    unique = list(dict.fromkeys(hits))
    if len(unique) == 1:
        return unique[0]
    return None


def _item_number_package(parsed, known: set[str]) -> str | None:
    index = None
    for position, header in enumerate(parsed.headers):
        if "品目号" in header and "名称" not in header and "编号" not in header:
            index = position
            break
    if index is None or not parsed.rows or index >= len(parsed.rows[0]):
        return None
    match = ITEM_NO_RE.match(parsed.rows[0][index])
    if match and match.group(1) in known:
        return match.group(1)
    return None


def _assign_isomorphic(notice: ParsedNotice, tables: HtmlTables, package_nos: list[str]) -> None:
    parsed_by_index = {table.table_index: table for table in notice.tables}
    groups: dict[tuple, list[HtmlTable]] = {}
    for understood in tables.tables:
        if understood.table_role not in {"cob_detail", "cob_summary"}:
            continue
        parsed = parsed_by_index.get(understood.table_index)
        if parsed is None:
            continue
        groups.setdefault(tuple(parsed.headers), []).append(understood)
    known = set(package_nos)
    for group in groups.values():
        if len(group) < 2:
            continue
        used = {item.package_scope for item in group if item.package_scope in known}
        unknown = [item for item in group if item.package_scope not in known and item.package_scope != "announcement"]
        remaining = [package for package in package_nos if package not in used]
        if unknown and len(unknown) == len(remaining):
            for item, package_no in zip(unknown, remaining):
                item.package_scope = package_no


def _ask_model(notice, pending, package_nos, llm, counter) -> None:
    parsed_by_index = {table.table_index: table for table in notice.tables}
    payload = {
        "known_packages": package_nos,
        "tables": [
            {
                "table_index": item.table_index,
                "table_role": item.table_role,
                "before_text": (parsed_by_index[item.table_index].before_text if item.table_index in parsed_by_index else ""),
                "headers": parsed_by_index[item.table_index].headers if item.table_index in parsed_by_index else [],
                "first_row": (parsed_by_index[item.table_index].rows[:1] if item.table_index in parsed_by_index else []),
            }
            for item in pending
        ],
    }

    def validate(parsed: dict) -> str:
        if not isinstance(parsed.get("assignments"), list):
            return "需要 assignments 数组"
        return ""

    try:
        parsed = complete_json(
            llm,
            step="resolve_packages",
            prompt_version="resolve-packages-v1",
            user=json.dumps(payload, ensure_ascii=False),
            validate=validate,
            counter=counter,
        )
    except Exception:
        return
    by_index = {item.table_index: item for item in pending}
    for assignment in parsed.get("assignments") or []:
        target = by_index.get(assignment.get("table_index"))
        scope = str(assignment.get("package_scope") or "")
        if target is not None and scope in set(package_nos):
            target.package_scope = scope
