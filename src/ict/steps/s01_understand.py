"""Step 1. The model proposes package structure; code recomputes amounts and project ids."""

from __future__ import annotations

import re

from ict.html_context import ParsedNotice
from ict.ids import make_project_id
from ict.llm import LLMClient, LLMError
from ict.money import parse_amount
from ict.schemas import Amount, AnnouncementUnderstanding, Failure, ModelMetadata, PackageUnderstanding

from ict.packages import registry_references


def _pack(text: str) -> str:
    return re.sub(r"\s+", "", text)


def checked_categories(raw_items, summary_text) -> tuple[list[str], list[str]]:
    """Keep only the 品目 entries the model transcribed from the announcement summary.

    公告概要里的品目只有这一个出口，而它的分隔符（逗号、顿号、分号、换行）没有稳定写法，切开
    这件事交给模型。规则只做结构校验：留下来的每一项都必须在它被抄的那段原文里连续出现，
    否则丢掉并报告——模型改写、补全或凭记忆写出来的条目进不到下游。
    """
    if not isinstance(raw_items, list):
        return [], []
    haystack = _pack(str(summary_text or ""))
    kept: list[str] = []
    dropped: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        text = str(item or "").strip()
        if not text:
            continue
        key = _pack(text)
        if not haystack or key not in haystack:
            dropped.append(text)
            continue
        if key in seen:                  # 原文自己会重复（如 C16990000,C16990000），不算抄错
            continue
        seen.add(key)
        kept.append(text)
    return kept, dropped


def _confidence(value, default: float = 0.9) -> float:
    """The model may write confidence as a word; it is an audit field, so never let it throw."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if 0 <= number <= 1 else default


def _amount(raw: str | None, scope: str, confidence: float | None = 0.9,
            amount_kind: str = "unknown", source_ref: str | None = None, unit_header: str | None = None) -> Amount | None:
    if not raw:
        return None
    yuan, _ = parse_amount(raw)
    if unit_header:
        from ict.money import parse_price_cell
        yuan = parse_price_cell(raw, unit_header)["value"]
    return Amount(raw_text=raw, amount_yuan=yuan, scope=scope, confidence=confidence,
                  amount_kind=amount_kind, source_ref=source_ref,
                  binding={"unit_header": unit_header} if unit_header else {})


def seal(run_id: str, proposed: dict, metadata: ModelMetadata | None, failures: list[Failure]) -> AnnouncementUnderstanding:
    name = (proposed.get("project_name") or "").strip()
    mode = proposed.get("package_mode") or "unclear"
    raw_packages = proposed.get("packages") or []
    packages: list[PackageUnderstanding] = []
    if not name:
        failures.append(Failure(failure_code="package_structure_unclear", failure_message="项目名称缺失"))
        mode = "unclear"
    for item, (package_no, source_no) in zip(raw_packages, registry_references(raw_packages)):
        evidence = item.get("package_evidence_text") or item.get("title") or package_no
        raw_amount = item.get("raw_amount") or (item.get("package_amount") or {}).get("raw_text")
        # Every other amount the model saw for this package, kept verbatim. Step 8 still receives
        # one package_amount; this only makes the choice it inherited visible. 22 of 49 packages
        # in the corpus state the amount in more than one place.
        alternatives: list[Amount] = []
        for entry in item.get("amount_alternatives") or []:
            if not isinstance(entry, dict):
                continue
            alt = _amount(entry.get("raw_text") or entry.get("raw_amount"), "package",
                          _confidence(entry.get("confidence")), entry.get("amount_kind", "unknown"),
                          entry.get("source_ref"), entry.get("unit_header"))
            if alt is not None:
                alternatives.append(alt)
        packages.append(
            PackageUnderstanding(
                package_no=package_no,
                source_package_no=source_no,
                package_kind=item.get("package_kind", "unknown"),
                package_name=item.get("package_name"),
                title=item.get("title"),
                project_id=make_project_id(name, package_no) if name else "",
                package_evidence_text=evidence,
                package_amount=_amount(raw_amount, "package", amount_kind=(item.get("package_amount") or {}).get("amount_kind", "unknown"),
                                       source_ref=(item.get("package_amount") or {}).get("source_ref"),
                                       unit_header=(item.get("package_amount") or {}).get("unit_header")),
                amount_alternatives=alternatives,
            )
        )
    if mode == "single" and not packages and name:
        packages = [
            PackageUnderstanding(
                package_no="1",
                title=name,
                project_id=make_project_id(name, "1"),
                package_evidence_text=name,
                package_amount=None,
            )
        ]
    if mode == "single" and len(packages) != 1:
        mode = "unclear"
        failures.append(Failure(failure_code="package_structure_unclear", failure_message="单包公告的包数量不是 1"))
    if mode == "multi" and len(packages) < 2:
        mode = "unclear"
        failures.append(Failure(failure_code="package_structure_unclear", failure_message="多包公告少于两个包"))
    if mode == "unclear" and not any(item.failure_code == "package_structure_unclear" for item in failures):
        failures.append(Failure(failure_code="package_structure_unclear", failure_message=proposed.get("unclear_reason")))
    status = "failed" if mode == "unclear" or not name else "success"
    if failures and status == "success":
        status = "partial"
    categories = [str(item).strip() for item in (proposed.get("announcement_categories") or [])
                  if isinstance(item, str) and str(item).strip()]
    return AnnouncementUnderstanding(
        run_id=run_id,
        status=status,
        project_name=name or None,
        purchaser=proposed.get("purchaser"),
        source_project_no=proposed.get("source_project_no"),
        announcement_type=proposed.get("announcement_type") or "unknown",
        package_mode=mode if mode in {"single", "multi", "unclear"} else "unclear",
        packages=[] if status == "failed" and not name else packages,
        summary_amount=_amount(proposed.get("summary_raw"), "announcement",
                               amount_kind=(proposed.get("summary_amount") or {}).get("amount_kind", "unknown"),
                               source_ref=(proposed.get("summary_amount") or {}).get("source_ref"),
                               unit_header=(proposed.get("summary_amount") or {}).get("unit_header")),
        announcement_categories=categories,
        unclear_reason=proposed.get("unclear_reason"),
        model_metadata=metadata,
        failures=failures,
    )


def read_announcement(run_id, notice, llm, counter=None, *, provisional=False):
    """Two independent full-context extractions, followed by one local assembly."""
    import json
    from concurrent.futures import ThreadPoolExecutor
    from ict.llm import complete_json
    from ict.observations import seal_observations
    from ict.announcement_receiving import AnnouncementReceiver
    from ict.schemas import CandidateFile, HtmlTable, HtmlTables
    from bs4 import BeautifulSoup

    contexts = [{"file_id": None, "page_no": None, "source_type": "html",
                 "source_segments": list(notice.source_contexts.values())}]
    branch_refs = {}
    for branch, html in (("summary", notice.summary_html), ("body", notice.body_html)):
        branch_refs[branch] = {tag["data-source-ref"] for tag in BeautifulSoup(html, "lxml").find_all(attrs={"data-source-ref": True})}
        branch_refs[branch].add("html:" + branch)

    def ask(branch):
        notes = {}
        receiver = AnnouncementReceiver(branch, contexts, branch_refs[branch], {table.table_index for table in notice.tables})
        try:
            if llm is None:
                raise LLMError("未配置模型")
            request = {"source_ref": "html:" + branch,
                       "html": notice.summary_html if branch == "summary" else notice.body_html}
            if provisional and branch == "body":
                request["任务说明"] = ("未明示包归属的供应商、成交额、得分保存在同一个顶层SUB观测。"
                    "供应商表与采购需求分包目录不得按顺序配对。仅写详见附件的汇总行不制造独立成交标的。"
                    "项目名或采购单元未知时仍保留能确认的事实。")
            value = complete_json(llm, step="read_announcement_" + branch,
                prompt_version="announcement-summary-v2" if branch == "summary" else "announcement-body-v6",
                user=json.dumps(request, ensure_ascii=False),
                validate=receiver.validate, receiver=receiver, counter=counter, notes=notes)
            failure = (Failure(failure_code="llm_schema_invalid", location=branch,
                               failure_message="独立观测存在未修复错误；见reception")
                       if value["reception"]["unrepaired"] else None)
            return value, notes, failure
        except (LLMError, ValueError) as exc:
            return {}, notes, Failure(failure_code="llm_call_failed" if isinstance(exc, LLMError) else "llm_schema_invalid",
                                      location=branch, failure_message=str(exc))

    with ThreadPoolExecutor(max_workers=2) as pool:
        summary_job, body_job = pool.submit(ask, "summary"), pool.submit(ask, "body")
        summary, summary_notes, summary_error = summary_job.result()
        body, body_notes, body_error = body_job.result()
    failures = [error for error in (summary_error, body_error) if error]
    proposed = dict(body)
    conflicts = []
    for key in ("project_name", "purchaser", "source_project_no", "announcement_type"):
        left, right = summary.get(key), body.get(key)
        selected = "body" if right not in (None, "", "unknown") else "summary"
        if provisional and left not in (None, "", "unknown") and right not in (None, "", "unknown") and _pack(left) != _pack(right):
            selected = None
        if left and right and left != right:
            conflicts.append({"field": key, "summary": left, "body": right, "selected": selected})
        proposed[key] = (right if selected == "body" else left) if selected else None
    raw_packages = body.get("packages", [])
    proposed["packages"] = [dict(item, package_no=item.get("source_package_no")) for item in raw_packages]
    proposed["summary_amount"] = summary.get("summary_amount")
    proposed["summary_raw"] = (summary.get("summary_amount") or {}).get("raw_text")
    categories, dropped = checked_categories(summary.get("announcement_categories"), notice.summary.get("品目"))
    proposed["announcement_categories"] = categories
    failures.extend(Failure(failure_code="category_item_not_in_summary", failure_message=text) for text in dropped)
    understanding = seal(run_id, proposed, None, failures)
    if provisional and not understanding.project_name and raw_packages:
        provisional_input = dict(proposed, project_name=run_id)
        understanding.packages = seal(run_id, provisional_input, None, []).packages
    understanding.branch_audit = {"summary": {"output": summary, **summary_notes},
                                  "body": {"output": body, **body_notes}, "conflicts": conflicts}
    candidates, seq = [], 1
    for raw, package in zip(raw_packages, understanding.packages):
        rows, seq = seal_observations(raw.get("candidates", []), contexts, understanding.project_name or run_id, seq, package=package)
        candidates.extend(rows)
    rows, seq = seal_observations(body.get("candidates", []), contexts, understanding.project_name or "", seq)
    candidates.extend(rows)
    has_facts = (candidates or understanding.packages or understanding.project_name
                 or understanding.purchaser or understanding.source_project_no or understanding.summary_amount
                 or understanding.announcement_categories)
    if provisional and understanding.status == "failed" and has_facts:
        understanding.status = "partial"
    tables = HtmlTables(run_id=run_id, status="partial" if body_error else "success", tables=[
        HtmlTable(**(row | {"package_scope": "announcement", "status": "success"})) for row in body.get("tables", [])])
    return understanding, tables, CandidateFile(run_id=run_id, status=understanding.status,
                                               candidates=candidates, failures=failures,
                                               reception=[{"branch": branch, **value["reception"]} for branch, value in
                                                          (("summary", summary), ("body", body)) if "reception" in value])
