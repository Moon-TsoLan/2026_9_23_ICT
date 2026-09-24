"""Steps 2A and 2B. Column roles may come from the model; rows are sealed in code."""

from __future__ import annotations

import json

from ict.candidates import seal_candidate
from ict.config import SOURCE_PRIORITY
from ict.html_context import ParsedNotice, step2a_payload
from ict.ids import make_project_id
from ict.llm import LLMClient, LLMError, parse_json_object
from ict.schemas import Candidate, Failure, HtmlTable, HtmlTables

def understand_tables(run_id: str, notice: ParsedNotice, package_nos: list[str], llm: LLMClient | None) -> HtmlTables:
    failures: list[Failure] = []
    tables: list[HtmlTable] = []
    if llm is None:
        failures.append(Failure(failure_code="llm_call_failed", failure_message="未配置 DeepSeek"))
        return HtmlTables(run_id=run_id, status="failed", tables=[], failures=failures)
    for table in notice.tables:
        try:
            result = llm.complete(
                step="understand_html_tables",
                prompt_version="html-tables-v1",
                user=json.dumps(step2a_payload(table, package_nos), ensure_ascii=False),
            )
            parsed = parse_json_object(result.text)
            role = parsed.get("table_role")
            if role not in {"cob_detail", "cob_summary", "sub_score", "winner", "agency_fee", "other"}:
                raise ValueError(f"table_role invalid: {role}")
            mapped = {
                key: value
                for key, value in (parsed.get("column_mapping") or {}).items()
                if value in table.headers
            }
            tables.append(
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
                )
            )
        except (LLMError, ValueError) as exc:
            code = "llm_call_failed" if isinstance(exc, LLMError) else "llm_schema_invalid"
            failures.append(Failure(failure_code=code, failure_message=str(exc), location=str(table.table_index)))
            tables.append(
                HtmlTable(
                    table_index=table.table_index,
                    table_role="other",
                    package_scope="unknown",
                    row_grain="other",
                    status="failed",
                )
            )
    status = "failed" if not tables else ("partial" if failures else "success")
    return HtmlTables(run_id=run_id, status=status, tables=tables, failures=failures)


def extract_html_candidates(
    run_id: str,
    notice: ParsedNotice,
    tables: HtmlTables,
    project_name: str,
    llm: LLMClient | None,
    seq_start: int = 1,
) -> tuple[list[Candidate], list[Failure], int]:
    failures: list[Failure] = []
    candidates: list[Candidate] = []
    seq = seq_start
    if llm is None:
        failures.append(Failure(failure_code="llm_call_failed", failure_message="未配置 DeepSeek"))
        return candidates, failures, seq
    by_index = {table.table_index: table for table in notice.tables}
    for understood in tables.tables:
        if understood.table_role in {"other", "agency_fee"} or understood.status == "failed":
            continue
        table = by_index.get(understood.table_index)
        if table is None:
            continue
        entity = "sub" if understood.table_role in {"sub_score", "winner"} else "cob"
        package_no = None if understood.package_scope in {"unknown", "announcement"} else understood.package_scope
        project_id = make_project_id(project_name, package_no) if package_no and project_name else None
        priority = SOURCE_PRIORITY.get(understood.table_role, 30)
        payload = {
            "entity_type": entity,
            "package_no": package_no,
            "table_role": understood.table_role,
            "column_mapping": understood.column_mapping,
            "headers": table.headers,
            "rows": table.rows,
        }
        try:
            result = llm.complete(
                step="extract_html_candidates",
                prompt_version="html-candidates-v1",
                user=json.dumps(payload, ensure_ascii=False),
            )
            parsed = parse_json_object(result.text)
        except (LLMError, ValueError) as exc:
            code = "llm_call_failed" if isinstance(exc, LLMError) else "llm_schema_invalid"
            failures.append(Failure(failure_code=code, failure_message=str(exc), location=str(understood.table_index)))
            continue
        for item in parsed.get("candidates") or []:
            item_type = item.get("entity_type") or entity
            if item_type not in {"cob", "sub"}:
                continue
            item_package = item.get("package_no") or package_no
            item_project = make_project_id(project_name, str(item_package)) if item_package and project_name else None
            if item.get("project_id") is None and item.get("package_no") is None:
                item_project = None
                item_package = None
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
                )
            )
            seq += 1
    if not candidates:
        failures.append(Failure(failure_code="no_candidate_extracted", failure_message="HTML 未抽出候选"))
    return candidates, failures, seq
