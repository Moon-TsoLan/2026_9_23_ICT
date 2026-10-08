"""Steps 2A and 2B. Column roles may come from the model; rows are sealed in code."""

from __future__ import annotations

import json
import re
from collections import Counter

from ict.candidates import align_fields, seal_candidate
from ict.concurrency import parallel_map
from ict.config import SOURCE_PRIORITY
from ict.html_context import ParsedNotice, ParsedTable, step2a_payload
from ict.ids import make_project_id
from ict.llm import LLMClient, LLMError, complete_json
from ict.package_resolve import promote_object_tables, resolve_table_packages
from ict.schemas import Candidate, Failure, FieldObservation, HtmlTable, HtmlTables

TABLE_ROLES = {"cob_detail", "cob_summary", "sub_score", "winner", "agency_fee", "other"}
ROW_GRAINS = {"cob", "supplier", "project", "other"}
LINE_KEYS = ("brand", "spec_model", "quantity", "unit_price", "total_price")
POINTER_RE = re.compile(r"详见|见附件")
LINE_POINTER_RE = re.compile(r"(?:品牌|规格|型号|数量|单价|总价|报价)\s*[:：]?\s*(?:详见|见)\S{0,8}?(?:附件|文件|明细)")


def _compact(value) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def _bidder_cells(table: ParsedTable, mapping: dict) -> list[str]:
    """Bidder column cells of an object table that has no manufacturer column."""
    header = mapping.get("supplier_name")
    if not header or "product_supplier" in mapping or header not in table.headers:
        return []
    column = table.headers.index(header)
    return [_compact(row[column]) for row in table.rows if column < len(row) and row[column]]


def _validate_table(parsed: dict, package_nos: list[str]) -> str:
    if parsed.get("table_role") not in TABLE_ROLES:
        return "table_role 不在允许值内"
    if parsed.get("row_grain") not in ROW_GRAINS and parsed.get("row_grain") not in (None, ""):
        return "row_grain 不在允许值内"
    scope = str(parsed.get("package_scope") or "unknown")
    if scope not in set(package_nos) | {"announcement", "unknown"}:
        return "package_scope 不在公告包列表、announcement、unknown 之中"
    return ""


def understand_tables(
    run_id: str,
    notice: ParsedNotice,
    package_nos: list[str],
    llm: LLMClient | None,
    counter: Counter | None = None,
) -> HtmlTables:
    failures: list[Failure] = []
    tables: list[HtmlTable] = []
    if llm is None:
        failures.append(Failure(failure_code="llm_call_failed", failure_message="未配置 DeepSeek"))
        return HtmlTables(run_id=run_id, status="failed", tables=[], failures=failures)

    def understand_one(table: ParsedTable) -> tuple[HtmlTable, Failure | None]:
        """One table in, one role out. Nothing here reads another table's result."""
        siblings = [
            {
                "table_index": other.table_index,
                "section": other.section,
                "before_text": other.before_text[:80],
                "headers": other.headers,
            }
            for other in notice.tables
            if other.table_index != table.table_index
        ]
        try:
            parsed = complete_json(
                llm,
                step="understand_html_tables",
                prompt_version="html-tables-v1",
                user=json.dumps(step2a_payload(table, package_nos, siblings), ensure_ascii=False),
                validate=lambda item: _validate_table(item, package_nos),
                counter=counter,
            )
            role = parsed.get("table_role")
            if role not in TABLE_ROLES:
                raise ValueError(f"table_role invalid: {role}")
            mapped = {
                key: value
                for key, value in (parsed.get("column_mapping") or {}).items()
                if value in table.headers
            }
            return (
                HtmlTable(
                    table_index=table.table_index,
                    table_role=role,
                    package_scope=str(parsed.get("package_scope") or "unknown"),
                    row_grain=parsed.get("row_grain") or "other",
                    column_mapping=mapped,
                    unmapped_columns=parsed.get("unmapped_columns") or [],
                    confidence=parsed.get("confidence"),
                    issues=parsed.get("issues") or [],
                    status="success",
                ),
                None,
            )
        except (LLMError, ValueError) as exc:
            code = "llm_call_failed" if isinstance(exc, LLMError) else "llm_schema_invalid"
            return (
                HtmlTable(
                    table_index=table.table_index,
                    table_role="other",
                    package_scope="unknown",
                    row_grain="other",
                    status="failed",
                ),
                Failure(failure_code=code, failure_message=str(exc), location=str(table.table_index)),
            )

    # One independent call per table. Results come back in table order, so the table list and the
    # failure list read exactly as the serial loop wrote them.
    for understood, failure in parallel_map(understand_one, notice.tables):
        tables.append(understood)
        if failure is not None:
            failures.append(failure)
    promote_object_tables(notice, HtmlTables(run_id=run_id, status="success", tables=tables))
    resolve_table_packages(notice, HtmlTables(run_id=run_id, status="success", tables=tables), package_nos, llm, counter)
    status = "failed" if not tables else ("partial" if failures else "success")
    return HtmlTables(run_id=run_id, status=status, tables=tables, failures=failures)


def extract_html_candidates(
    run_id: str,
    notice: ParsedNotice,
    tables: HtmlTables,
    project_name: str,
    llm: LLMClient | None,
    seq_start: int = 1,
    counter: Counter | None = None,
    known_packages: list[str] | None = None,
) -> tuple[list[Candidate], list[Failure], int]:
    failures: list[Failure] = []
    candidates: list[Candidate] = []
    seq = seq_start
    if llm is None:
        failures.append(Failure(failure_code="llm_call_failed", failure_message="未配置 DeepSeek"))
        return candidates, failures, seq
    by_index = {table.table_index: table for table in notice.tables}

    def ask(understood: HtmlTable) -> dict:
        """One table's model call and the read-only facts its candidates need. No shared writes."""
        table = by_index[understood.table_index]
        entity = "sub" if understood.table_role in {"sub_score", "winner"} else "cob"
        package_no = None if understood.package_scope in {"unknown", "announcement"} else understood.package_scope
        project_id = make_project_id(project_name, package_no) if package_no and project_name else None
        priority = SOURCE_PRIORITY.get(understood.table_role, 30)
        if understood.table_role == "cob_detail" and "主要标的" in (table.section or ""):
            priority = 100
        payload = {
            "entity_type": entity,
            "package_no": package_no,
            "table_role": understood.table_role,
            "column_mapping": understood.column_mapping,
            "headers": table.headers,
            "rows": table.rows,
        }
        try:
            parsed = complete_json(
                llm,
                step="extract_html_candidates",
                prompt_version="html-candidates-v1",
                user=json.dumps(payload, ensure_ascii=False),
                validate=lambda item: "" if isinstance(item.get("candidates"), list) else "需要 candidates 数组",
                counter=counter,
            )
        except (LLMError, ValueError) as exc:
            code = "llm_call_failed" if isinstance(exc, LLMError) else "llm_schema_invalid"
            return {"understood": understood, "failure": Failure(
                failure_code=code, failure_message=str(exc), location=str(understood.table_index))}
        return {
            "understood": understood,
            "table": table,
            "entity": entity,
            "package_no": package_no,
            "priority": priority,
            "parsed": parsed,
            "pointer": entity == "cob" and _line_fields_point(table, understood.column_mapping),
            "cells": ({_compact(cell) for row in table.rows for cell in row}
                      if understood.table_role == "winner" else set()),
            "bidders": _bidder_cells(table, understood.column_mapping) if entity == "cob" else [],
            "failure": None,
        }

    asked_tables = [understood for understood in tables.tables
                    if understood.table_role not in {"other", "agency_fee"}
                    and understood.status != "failed"
                    and understood.table_index in by_index]
    # One independent call per table; the sealing below stays in the parent so candidate ids keep
    # the order the serial loop gave them.
    for asked in parallel_map(ask, asked_tables):
        if asked["failure"] is not None:
            failures.append(asked["failure"])
            continue
        understood = asked["understood"]
        start = len(candidates)
        seq = _append_candidates(
            candidates,
            asked["parsed"].get("candidates") or [],
            asked["entity"],
            asked["package_no"],
            project_name,
            asked["priority"],
            seq,
            known_packages,
            table=asked["table"],
            table_index=understood.table_index,
            column_mapping=understood.column_mapping,
        )
        pointer = asked["pointer"]
        cells = asked["cells"]
        bidders = asked["bidders"]
        for candidate in candidates[start:]:
            name = candidate.fields.get("supplier_name")
            if candidate.entity_type == "sub" and name and _compact(name.raw_value) in cells:
                candidate.fields["is_winner"] = FieldObservation(raw_value=True, status="present")
            product = candidate.fields.get("product_supplier")
            if product and product.raw_value and any(_compact(product.raw_value) in cell for cell in bidders):
                candidate.fields["product_supplier"] = FieldObservation(raw_value=None, status="missing")
                candidate.issues.append("product_supplier_from_bidder_column")
            if pointer and candidate.entity_type == "cob" and "line_fields_point_to_attachment" not in candidate.issues:
                candidate.issues.append("line_fields_point_to_attachment")
    # 2026-10-08 用户指示：正文不再筛选，整篇一次交给模型。
    # 原来是 bidder_body_sections() 按"公司/供应商 + （数字"挑段落，实测把 65% 的公告挑成 0 段，
    # 而 t20260206_26155241 的「八、其它补充事宜」里明明写着「（第1包）…中标人…：宁夏隆昆…85.32分」。
    # 全篇正文中位只有 533 字、最长 5583 字，一次给的代价可以忽略。
    body_sections = [{"title": section["title"], "text": section["text"]}
                     for section in notice.sections]
    if body_sections:
        payload = {
            "entity_type": "sub",
            "package_no": None,
            "table_role": "sub_score",
            "column_mapping": {},
            "headers": [],
            "rows": [],
            "body_sections": body_sections,
        }
        try:
            parsed = complete_json(
                llm,
                step="extract_html_candidates",
                prompt_version="html-candidates-v1",
                user=json.dumps(payload, ensure_ascii=False),
                validate=lambda item: "" if isinstance(item.get("candidates"), list) else "需要 candidates 数组",
                counter=counter,
            )
        except (LLMError, ValueError) as exc:
            code = "llm_call_failed" if isinstance(exc, LLMError) else "llm_schema_invalid"
            failures.append(Failure(failure_code=code, failure_message=str(exc), location="body_sections"))
        else:
            seq = _append_candidates(
                candidates,
                parsed.get("candidates") or [],
                "sub",
                None,
                project_name,
                SOURCE_PRIORITY["sub_score"],
                seq,
                known_packages,
            )
    if not candidates:
        failures.append(Failure(failure_code="no_candidate_extracted", failure_message="HTML 未抽出候选"))
    return candidates, failures, seq


def _pointer_cell(text: str) -> bool:
    return bool(POINTER_RE.search(text or "")) and len(text) <= 20


def _line_fields_point(table: ParsedTable, mapping: dict[str, str]) -> bool:
    """Brand, spec, quantity or price cells that only say 'see attachment'."""
    for key in LINE_KEYS:
        header = mapping.get(key)
        if header not in table.headers:
            continue
        column = table.headers.index(header)
        if any(column < len(row) and _pointer_cell(row[column]) for row in table.rows):
            return True
    return any(LINE_POINTER_RE.search(cell or "") for row in table.rows for cell in row)


def _column_units(mapping: dict) -> dict:
    """The header text behind each amount field, straight out of step 2A's column mapping.

    Only the two price fields matter: a quantity or a unit never carries a 万元.
    """
    return {key: str(mapping[key]).strip() for key in ("unit_price", "total_price")
            if mapping.get(key) not in (None, "")}


def _row_text(table: ParsedTable | None, raw_fields: dict) -> str | None:
    """The source line, and only when exactly one row of the table carries these values.

    Step 8 needs the line a value came from and cannot recover it later: a candidate does not
    know its own row. The lookup is mechanical, and an ambiguous match is left empty rather
    than guessed at.
    """
    if table is None:
        return None
    wanted = _compact(raw_fields.get("object_name") or raw_fields.get("supplier_name") or "")
    if not wanted:
        return None
    hits = [row for row in table.rows if any(wanted in _compact(cell) for cell in row)]
    if len(hits) != 1:
        return None
    cells = [str(cell) for cell in hits[0] if str(cell).strip()]
    return " | ".join(cells)[:400] or None


def _append_candidates(candidates, items, entity, package_no, project_name, priority, seq, known_packages=None,
                       table: ParsedTable | None = None, table_index: int | None = None,
                       column_mapping: dict | None = None) -> int:
    for item in items:
        item_type = item.get("entity_type") or entity
        if item_type not in {"cob", "sub"}:
            continue
        if not any(value not in (None, "") for value in (item.get("fields") or {}).values()):
            continue
        item_package = item.get("package_no") or package_no
        if item_package in (None, "", "null") and known_packages and len(known_packages) == 1:
            item_package = known_packages[0]
        item_project = make_project_id(project_name, str(item_package)) if item_package and project_name else None
        candidates.append(
            seal_candidate(
                candidate_id=f"cand_{seq:06d}",
                entity_type=item_type,
                project_id=item_project,
                package_no=None if item_package is None else str(item_package),
                source_type="html",
                file_id=None,
                source_priority=priority,
                raw_fields=item.get("fields") or {},
                issues=item.get("issues") or [],
                table_index=table_index,
                row_text=_row_text(table, align_fields(item_type, item.get("fields") or {})),
                column_units=_column_units(column_mapping or {}) if item_type == "cob" else None,
            )
        )
        seq += 1
    return seq
