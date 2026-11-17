# 模型调用的实际payload构造函数快照

只读静态摘录，函数中包含当前上下文选择、payload组装、调用与接收。

## understand_announcement — src/ict/steps/s01_understand.py:183
```python
def understand(run_id: str, notice: ParsedNotice, llm: LLMClient | None, counter=None) -> AnnouncementUnderstanding:
    import json

    from ict.html_context import fill_package_amounts, step1_payload
    from ict.llm import complete_json

    failures: list[Failure] = []
    if llm is None:
        failures.append(Failure(failure_code="llm_call_failed", failure_message="未配置 DeepSeek"))
        return seal(run_id, {"package_mode": "unclear", "unclear_reason": "未配置 DeepSeek"}, None, failures)
    try:
        proposed = complete_json(
            llm,
            step="understand_announcement",
            prompt_version=PROMPT,
            user=json.dumps(step1_payload(notice), ensure_ascii=False),
            validate=_validate_announcement,
            counter=counter,
        )
    except (LLMError, ValueError) as exc:
        code = "llm_call_failed" if isinstance(exc, LLMError) else "llm_schema_invalid"
        failures.append(Failure(failure_code=code, failure_message=str(exc)))
        return seal(run_id, {"package_mode": "unclear", "unclear_reason": str(exc)}, None, failures)
    metadata = ModelMetadata(
        model_name=getattr(llm, "model", "configured"),
        model_version=getattr(llm, "model", "configured"),
        prompt_version=PROMPT,
        latency_ms=0,
    )
    if "summary_raw" not in proposed:
        summary = proposed.get("summary_amount") or {}
        proposed["summary_raw"] = summary.get("raw_text") if isinstance(summary, dict) else None
    kept, dropped = checked_categories(proposed.get("announcement_categories"),
                                       notice.summary.get("品目"))
    proposed["announcement_categories"] = kept
    for text in dropped:
        failures.append(Failure(failure_code="category_item_not_in_summary",
                                failure_message="公告概要里没有这一段：" + text))
    understanding = seal(run_id, proposed, metadata, failures)
    fill_package_amounts(understanding, notice)
    return understanding
```

## understand_html_tables — src/ict/steps/s02_html.py:71
```python
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
                prompt_version="html-tables-v6",
                user=json.dumps((step2a_payload(table, package_nos, siblings) | {"package_registry": package_registry or []}), ensure_ascii=False),
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
```

## extract_html_candidates — src/ict/steps/s02_html.py:172
```python
    def ask(understood: HtmlTable) -> dict:
        """One table's model call and the read-only facts its candidates need. No shared writes."""
        table = by_index[understood.table_index]
        entity = "sub" if understood.table_role in {"sub_score", "sub_info", "winner"} else "cob"
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
            "source_rows": html_rows(table),
            "known_packages": known_packages or [],
            "package_registry": package_registry or [],
            "section": table.section,
            "before_text": table.before_text,
        }
        try:
            parsed = complete_json(
                llm,
                step="extract_html_candidates",
                prompt_version="html-candidates-v5",
                user=json.dumps(payload, ensure_ascii=False),
                validate=lambda item: _validate_source_candidates(item, payload["source_rows"]),
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
```

## extract_html_candidates — src/ict/steps/s02_html.py:153
```python
def extract_html_candidates(
    run_id: str,
    notice: ParsedNotice,
    tables: HtmlTables,
    project_name: str,
    llm: LLMClient | None,
    seq_start: int = 1,
    counter: Counter | None = None,
    known_packages: list[str] | None = None,
    package_registry: list[dict] | None = None,
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
        entity = "sub" if understood.table_role in {"sub_score", "sub_info", "winner"} else "cob"
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
            "source_rows": html_rows(table),
            "known_packages": known_packages or [],
            "package_registry": package_registry or [],
            "section": table.section,
            "before_text": table.before_text,
        }
        try:
            parsed = complete_json(
                llm,
                step="extract_html_candidates",
                prompt_version="html-candidates-v5",
                user=json.dumps(payload, ensure_ascii=False),
                validate=lambda item: _validate_source_candidates(item, payload["source_rows"]),
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
    body_sections = [{"source_ref": f"html:body:{index}", "title": section["title"], "text": section["text"]}
                     for index, section in enumerate(notice.sections)]
    if body_sections:
        payload = {
            "entity_type": "sub",
            "package_no": None,
            "table_role": "sub_score",
            "column_mapping": {},
            "headers": [],
            "rows": [],
            "body_sections": body_sections,
            "known_packages": known_packages or [],
            "package_registry": package_registry or [],
        }
        try:
            parsed = complete_json(
                llm,
                step="extract_html_candidates",
                prompt_version="html-candidates-v5",
                user=json.dumps(payload, ensure_ascii=False),
                validate=lambda item: _validate_source_candidates(item, body_sections),
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
                body_sources=body_sections,
            )
    if not candidates:
        failures.append(Failure(failure_code="no_candidate_extracted", failure_message="HTML 未抽出候选"))
    return candidates, failures, seq
```

## locate_pages — src/ict/steps/s04_attach.py:366
```python
def locate_pages(run_id: str, plans: ProjectPlans, files: FileDecisions, parsed: dict[str, list[dict]],
                 index: AttachmentIndex, llm: LLMClient | None, counter=None) -> PageDecisions:
    """Step 5. Choose pages from pages we already have, so page numbers stay true."""
    chosen = [item for item in files.file_decisions if item.read_strategy == "target_pages" and item.file_id in parsed]
    if not chosen:
        return PageDecisions(run_id=run_id, status="skipped", failures=list(files.failures))
    if llm is None:
        return PageDecisions(run_id=run_id, status="failed",
                             failures=[Failure(failure_code="llm_call_failed", failure_message="未配置模型")])
    known_packages = [project.package_no for project in plans.projects]
    display_names = {item.file_id: item.display_name for item in index.files}
    page_inputs = []
    for decision in chosen:
        pages = parsed[decision.file_id]
        slim = []
        for page in pages:
            rows = [row for table in page["tables"] for row in table["rows"]]
            if page["chars"] < 20 and not rows:
                continue
            slim.append({"page_no": page["page_no"], "text_head": page["text"][:PAGE_TEXT_HEAD],
                         "table_headers": [table["headers"] for table in page["tables"]],
                         "table_rows": len(rows),
                         "row_sample": " | ".join(rows[0][:8]) if rows else "",
                         "tables": len(page["tables"]), "chars": page["chars"]})
        page_inputs.append({"file_id": decision.file_id, "display_name": display_names.get(decision.file_id, decision.file_id),
                            "file_class": decision.file_class, "possible_packages": decision.possible_packages,
                            "found_fields": decision.expected_fields, "pages": slim[:PAGE_LOCATE_LIMIT],
                            "native_evidence": {"segments": [segment for segment in decision.native_segments
                                                           if segment["source_ref"] in decision.evidence_refs],
                                                "reuse_status": decision.reuse_status,
                                                "needs": [need for plan in plans.projects for need in plan.needs
                                                          if need["field"] in decision.expected_fields]}})
    try:
        produced = complete_json(
            llm, step="locate_pages", prompt_version="locate-pages-v5",
            user=json.dumps({"known_packages": known_packages,
                             "package_registry": [package_context(p) for p in plans.projects],
                             "needs": [entry for project in plans.projects for entry in (project.needs or [])],
                             "files": page_inputs}, ensure_ascii=False),
            validate=_validate_pages, counter=counter)
    except (LLMError, ValueError) as exc:
        code = "llm_call_failed" if isinstance(exc, LLMError) else "llm_schema_invalid"
        return PageDecisions(run_id=run_id, status="failed", failures=[Failure(failure_code=code, failure_message=str(exc))])
    name_by_file = {}
    for item in index.files:
        name_by_file[item.file_id] = item.display_name
    mentioned: dict[tuple[str, int], set[str]] = {}
    for file_id, pages in parsed.items():
        for page in pages:
            mentioned[(file_id, page["page_no"])] = page_packages(page["text"], known_packages, plans.projects)
    decisions: list[PageDecision] = []
    per_file: dict[str, int] = {}
    for item in produced.get("page_decisions") or []:
        file_id = item.get("file_id")
        if file_id not in parsed:
            continue
        per_file[file_id] = per_file.get(file_id, 0) + 1
        if per_file[file_id] > MAX_PAGES_PER_FILE or len(decisions) >= MAX_PAGES_PER_ANNOUNCEMENT:
            continue
        mode = item.get("extraction_mode") or "text"
        if mode not in {"text", "table", "text_and_table", "unsupported"}:
            mode = "text"
        page_no = int(item.get("page_no") or 0)
        meta = next((entry for entry in page_inputs if entry["file_id"] == file_id), {})
        scope = coerce_package_scope(str(item.get("package_scope") or "unknown"),
                                     list(meta.get("possible_packages") or []), known_packages,
                                     name_by_file.get(file_id, ""), plans.projects)
        if len(mentioned.get((file_id, page_no), set())) >= 2:
            scope = "announcement"
        decisions.append(PageDecision(file_id=file_id, page_no=page_no,
                                      relevance=float(item.get("relevance") or 0),
                                      expected_fields=item.get("expected_fields") or [],
                                      package_scope=scope, extraction_mode=mode,
                                      reason=item.get("reason") or ""))
    status = "partial" if not decisions else "success"
    return PageDecisions(run_id=run_id, status=status, page_decisions=decisions)
```

## extract_attachment_candidates — src/ict/steps/s04_attach.py:506
```python
    def ask(job: dict) -> dict:
        """One package chunk in, one model answer out. Reads only its own chunk and the plan."""
        package_no, chunk = job["package_no"], job["chunk"]
        plan = plans_by_no.get(package_no)
        payload = {"current_package": {
            "project_id": None if plan is None else plan.project_id,
            **({} if plan is None else package_context(plan)),
            "package_no": None if package_no in {"unknown", "announcement"} else package_no,
            "missing_fields": list(dict.fromkeys(field for item in plans.projects for field in item.missing_fields))
                              if plan is None else plan.missing_fields,
            "needs": [need for item in plans.projects for need in item.needs]
                     if plan is None else (plan.needs or []),
            # Already established upstream, so step 6 copies it instead of re-deriving it.
            # Empty means nobody knows yet and the page is the only place left to look.
            "winner_supplier": _known_winner(package_no, chunk, winner_suppliers,
                                             file_quote_suppliers, file_award_notices)},
            "page_contexts": chunk,
            "known_packages": [{**package_context(project), "project_id": project.project_id,
                                 "needs": project.needs} for project in plans.projects]}
        payload["native_reuse"] = any(":native:" in segment["source_ref"] for context in chunk
                                      for segment in context["source_segments"])
        try:
            from ict.receiving import ExtractionReceiver
            produced = complete_json(
                llm, step="extract_attachment_candidates", prompt_version="attachment-extract-v5",
                user=json.dumps(payload, ensure_ascii=False), validate=_validate_candidates, counter=counter,
                receiver=ExtractionReceiver(chunk, needs=payload["current_package"]["needs"]))
        except (LLMError, ValueError) as exc:
            code = "llm_call_failed" if isinstance(exc, LLMError) else "llm_schema_invalid"
            return {**job, "payload": payload, "produced": None,
                    "failure": Failure(failure_code=code, failure_message=str(exc)[:200],
                                       location=package_no)}
        return {**job, "payload": payload, "produced": produced, "failure": None}
```

## review_native_continuation — src/ict/steps/s04_attach.py:696
```python
def review_native_continuation(before, after, decisions, candidates, amounts, llm, counter=None, *, reception=None):
    """One file-local model review of a no-deep suggestion after reception and rebinding.

    This never resolves any global need. Not-attempted, rejected and failed reviews keep the
    deep path; a successful attempt with no new observations needs an explicit model decision.
    """
    from ict.llm import ValidationIssue
    failures = []
    before_needs = [need for plan in before.projects for need in plan.needs]
    after_needs = [need for plan in after.projects for need in plan.needs]
    known_ids = {need["need_id"] for need in before_needs + after_needs}
    known_packages = {plan.package_no for plan in after.projects}
    for decision in decisions.file_decisions:
        if decision.read_strategy != "target_pages":
            continue
        screen = getattr(decision, "screen", {}) or {}
        decision.reuse_status = "native_evidence_partial"
        if not decision.evidence_refs or screen.get("requires_deep_parse", True):
            decision.continuation_audit = {"continuation": "deep_parse", "reason": "screen_requires_deep_or_no_cited_evidence"}
            continue
        cited = set(decision.evidence_refs)
        attempted_refs = set()
        rejected_refs = set()
        receipts = []
        for receipt in reception or []:
            if cited & set(receipt.get("source_refs", [])):
                receipts.append(receipt)
                for rejected in receipt.get("rejected_items", []):
                    raw = rejected.get("raw")
                    if isinstance(raw, dict) and str(rejected.get("path", "")).startswith(("candidates[", "package_amounts[")):
                        refs = raw.get("source_refs")
                        refs = refs if isinstance(refs, list) else []
                        rejected_refs.update(ref for ref in [raw.get("source_ref")] + refs if isinstance(ref, str))
                        if raw.get("file_id") == decision.file_id:
                            rejected_refs.update(cited)
                attempts = receipt.get("attempts", [])
                if attempts and not attempts[-1].get("issues") and not receipt.get("unrepaired"):
                    attempted_refs.update(receipt.get("source_refs", []))
        observations = []
        related = []
        accepted_refs = set()
        for candidate in candidates:
            refs = set(candidate.evidence.source_refs if candidate.evidence else []) & cited
            fields = {key: obs.model_dump() for key, obs in candidate.fields.items() if obs.status == "present"
                      and (obs.normalized_value if obs.normalized_value is not None else obs.raw_value) not in (None, "")}
            if refs and fields:
                observations.append({"candidate_id": candidate.candidate_id, "entity_type": candidate.entity_type,
                                     "package_no": candidate.package_no, "fields": fields, "source_refs": sorted(refs)})
                if candidate.package_no in known_packages and candidate.binding.get("status") != "unresolved":
                    accepted_refs.update(refs)
            elif fields:
                related.append({"candidate_id": candidate.candidate_id, "entity_type": candidate.entity_type,
                                "package_no": candidate.package_no, "fields": fields,
                                "source_refs": candidate.evidence.source_refs if candidate.evidence else []})
        for amount in amounts:
            if amount.file_id == decision.file_id and amount.source_ref in cited:
                observations.append(amount.model_dump() | {"entity_type": "package_amount"})
                if amount.package_no in known_packages and amount.amount_yuan is not None and amount.binding.get("status") != "unresolved":
                    accepted_refs.add(amount.source_ref)
        attempted_no_new = not observations and cited <= attempted_refs and not (cited & rejected_refs)
        if (not accepted_refs and not attempted_no_new) or llm is None:
            decision.continuation_audit = {"continuation": "deep_parse", "reason": "no_bound_accepted_evidence_or_model"}
            continue
        payload = {"file_id": decision.file_id, "package_registry": [package_context(p) for p in after.projects],
                   "needs_before": before_needs, "needs_after": after_needs,
                   "screen_observation": screen | {"source_segments": decision.native_segments},
                   "accepted_observations": observations, "related_observations": related,
                   "reception_status": {"source_refs_successfully_attempted": sorted(cited & attempted_refs),
                                        "no_new_observation": attempted_no_new,
                                        "receipts": receipts}}

        def validate(value):
            if value.get("continuation") not in {"finish", "deep_parse"}:
                return ValidationIssue("只能为finish或deep_parse", path="$.continuation")
            ids = value.get("needs_hit_ids", [])
            if not isinstance(ids, list) or any(not isinstance(i, str) or i not in known_ids for i in ids):
                return ValidationIssue("只能引用输入need_id", path="$.needs_hit_ids")
            refs = value.get("evidence_refs", [])
            basis = value.get("evidence_basis", "accepted_observation")
            allowed_refs = accepted_refs
            if basis == "attempted_no_new_observation":
                if not attempted_no_new:
                    return ValidationIssue("并非本文件成功尝试且无新观测，不能使用空结果依据", path="$.evidence_basis")
                allowed_refs = cited & attempted_refs
            elif basis != "accepted_observation":
                return ValidationIssue("依据只能为accepted_observation或attempted_no_new_observation", path="$.evidence_basis")
            if not isinstance(refs, list) or any(not isinstance(ref, str) or ref not in allowed_refs for ref in refs):
                return ValidationIssue("只能引用本文件已接收且归属有效的观测来源", path="$.evidence_refs")
            if value["continuation"] == "finish" and not refs:
                return ValidationIssue("结束本文件须引用本文件已接收的证据", path="$.evidence_refs")
            if not isinstance(value.get("reason"), str) or not value["reason"].strip():
                return ValidationIssue("需要说明本文件是否还需读取", path="$.reason")
            if not isinstance(value.get("kind"), str) or not isinstance(value.get("confidence"), (int, float)):
                return ValidationIssue("需要kind与confidence供审计，confidence不参与取舍")
            return ""

        notes = {}
        try:
            answer = complete_json(llm, step="review_native_continuation", prompt_version="native-continuation-v1",
                                   user=json.dumps(payload, ensure_ascii=False), validate=validate, counter=counter, notes=notes)
            decision.continuation_audit = {"input": payload, "output": answer, "attempts": notes.get("attempts", []),
                                           "calls": notes.get("calls", [])}
            if answer["continuation"] == "finish":
                decision.read_strategy = "skip"
                decision.reuse_status = "native_evidence_ready"
                decision.reason += ":model_native_complete"
        except (LLMError, ValueError) as exc:
            decision.continuation_audit = {"input": payload, "continuation": "deep_parse", "error": str(exc),
                                           "attempts": notes.get("attempts", getattr(exc, "attempts", [])),
                                           "calls": notes.get("calls", [])}
            failures.append(Failure(failure_code="llm_call_failed" if isinstance(exc, LLMError) else "llm_schema_invalid",
                                    failure_message="native_continuation:" + str(exc)[:180], location=decision.file_id))
    return failures
```

## repair_packages — src/ict/steps/s08b_repair.py:71
```python
def _ask_model(project: Project, alternatives: list[dict], violations: list[dict], llm: LLMClient, counter: Counter | None) -> list[dict]:
    payload = {
        **package_context(project),
        "package_no": project.package_no,
        "package_total_amount": project.package_total_amount,
        "violations": violations,
        "clusters": [
            {
                "cluster_id": item["cluster_id"],
                "object_name": item["object_name"],
                "current_candidate_id": item["chosen"],
                "options": item["options"],
            }
            for item in alternatives
        ],
    }
    allowed = {item["cluster_id"]: {option["candidate_id"] for option in item["options"]} for item in alternatives}

    def validate(parsed: dict) -> str:
        choices = parsed.get("choices")
        if not isinstance(choices, list):
            return "需要 choices 数组"
        for choice in choices:
            if choice.get("cluster_id") not in allowed:
                return f"cluster_id {choice.get('cluster_id')} 不在输入里"
            if choice.get("candidate_id") not in allowed[choice["cluster_id"]]:
                return f"candidate_id {choice.get('candidate_id')} 不是该簇的备选"
        return ""

    parsed = complete_json(
        llm,
        step="repair_packages",
        prompt_version="repair-package-v1",
        user=json.dumps(payload, ensure_ascii=False),
        validate=validate,
        counter=counter,
    )
    return parsed.get("choices") or []
```

## merge_candidates — src/ict/steps/s08_merge.py:517
```python
def _ask_merge(ctx: _Context, groups: list[Group], cobs: list[Candidate], amount: float | None,
               raw: str | None, suspect: str | None, evidence: MergeEvidence | None,
               llm: LLMClient, counter,
               categories: list[str] | None = None, amount_evidence=None) -> tuple[dict | None, dict | None, str | None]:
    by_group = {candidate.candidate_id: group.group_id for group in groups for candidate in group.members}
    payload = {
        "project_name": ctx.project_name,
        "package_no": ctx.package_no,
        "package_total_amount": amount,
        "package_amount_raw": raw,
        "package_amount_suspect": suspect is not None,
        "winner_suppliers": sorted(ctx.winners),
        "baseline_groups": [{"group_id": group.group_id,
                             "candidate_ids": [item.candidate_id for item in group.members]}
                            for group in groups],
        "candidates": [_candidate_view(item, by_group.get(item.candidate_id, -1), ctx, evidence)
                       for item in sorted(cobs, key=lambda entry: _rank(entry, ctx))],
    }
    if categories:
        # 公告概要抄下来的品目，原文给到模型；选哪条属于内容判断，编码与名称由规则查目录补。
        payload["announcement_categories"] = list(categories)
    by_id = {item.candidate_id: item for item in cobs}
    category_hits = {item: _catalog().match(None, item) for item in (categories or [])}
    allowed_amount = amount if suspect is None else None
    payload["protocol_version"] = "delta-v5"
    payload["package_amount_bindable"] = allowed_amount is not None
    payload["package_amount_evidence"] = amount_evidence or {}
    if evidence is not None:
        payload["package_registry"] = evidence.package_registry
        payload["evidence_context"] = {"attachment_pages": evidence.attachment_pages,
                                       "native_extraction": evidence.native_extraction,
                                       "amount_observations": evidence.amount_observations.get(ctx.package_no, [])}

    def validate(parsed):
        invalid["parsed"] = parsed
        try:
            plan = compile_plan(groups, by_id, parsed, lambda item: _ownership(item, ctx), _value,
                                _sum_bundle, allowed_amount, category_hits, amount_evidence)
            materialize(plan, lambda item: _ownership(item, ctx), _value,
                        lambda item: _rank(item, ctx), ctx.package_no)
        except PlanSchemaError as exc:
            return ValidationIssue(str(exc), "schema", "merge_plan")
        except PlanBusinessError as exc:
            return ValidationIssue(str(exc), "business", "merge_plan")
        except ValueError as exc:
            return ValidationIssue(str(exc), "plan_constraint", "merge_plan")
        return ""
    notes: dict = {}
    invalid: dict = {}
    try:
        produced = complete_json(llm, step="merge_candidates", prompt_version=PROMPT,
                                 user=json.dumps(payload, ensure_ascii=False),
                                 validate=validate,
                                 counter=counter, thinking=MERGE_THINKING, notes=notes)
    except (LLMError, ValueError) as exc:
        category = exc.issues[-1].category if isinstance(exc, LLMValidationError) else "transport"
        return None, {"code": "llm_call_failed" if isinstance(exc, LLMError) else
                      "merge_conflict_unresolved" if category == "business" else
                      "llm_validator_incompatible" if category == "implementation" else
                      "llm_plan_invalid" if category == "plan_constraint" else "llm_schema_invalid",
                      "category": category, "attempts": getattr(exc, "attempts", []),
                      "detail": str(exc), "deltas": invalid.get("parsed", {}).get("deltas", [])}, None
    produced["_validation_attempts"] = notes.get("attempts", [])
    return produced, None, (notes.get("reasoning") or None)
```

## complete_suppliers — src/ict/steps/s09_supplier.py:15
```python
    def one(project):
        eligible = {}
        for index, cob in enumerate(project.cobs):
            forbidden = any(binding.get("package_no") == project.package_no
                            and binding.get("cob_index") == index
                            and binding.get("field") == "product_supplier"
                            and binding.get("forbid_inference") for binding in merged.field_bindings)
            if not cob.brand and not cob.product_supplier and not forbidden:
                eligible[f"{project.project_id}:cob:{index}"] = index
        winners = {f"winner:{index}": sub.supplier_name for index, sub in enumerate(project.subs) if sub.is_winner}
        if not eligible or not winners or llm is None:
            return [], []
        payload = {"project_name": project.project_name, **package_context(project),
                   "winner_suppliers": [{"winner_ref": ref, "supplier_name": name} for ref, name in winners.items()],
                   "final_cobs": [{"cob_ref": ref, **project.cobs[index].model_dump()} for ref, index in eligible.items()],
                   "field_bindings": [item for item in merged.field_bindings if item.get("package_no") == project.package_no]}

        def validate(parsed):
            decisions = parsed.get("decisions")
            if not isinstance(decisions, list) or any(not isinstance(item, dict) for item in decisions):
                return "需要 decisions 数组"
            if (any(not isinstance(item.get("cob_ref"), str) for item in decisions)
                    or len(decisions) != len(eligible) or {item.get("cob_ref") for item in decisions} != set(eligible)):
                return "逐一覆盖所有待补 COB，不得重复或遗漏"
            for decision in decisions:
                if (not isinstance(decision.get("decision"), str)
                        or decision.get("decision") not in {"confirm", "reject", "uncertain"}
                        or not isinstance(decision.get("reason"), str) or not decision["reason"].strip()):
                    return "decision 必须为 confirm/reject/uncertain，并写 reason"
                if decision.get("decision") == "confirm" and (
                        not isinstance(decision.get("winner_ref"), str) or decision.get("winner_ref") not in winners):
                    return "确认自供只能引用本包提供的 winner_ref"
            return ""

        try:
            result = complete_json(llm, step="complete_suppliers", prompt_version="supplier-completion-v1",
                                   user=json.dumps(payload, ensure_ascii=False), validate=validate, counter=counter)
        except (LLMError, ValueError) as exc:
            return ([{"package_no": project.package_no, "decision": "failed", "reason": str(exc)}],
                    [Failure(failure_code="llm_schema_invalid", location=project.package_no, failure_message=str(exc))])
        audit = []
        for decision in result["decisions"]:
            index = eligible[decision["cob_ref"]]
            entry = {"package_no": project.package_no, "cob_index": index, **decision, "applied": False}
            if decision["decision"] == "confirm":
                name = winners[decision["winner_ref"]]
                project.cobs[index].product_supplier = name
                entry.update(applied=True, value=name, method="inferred_winner_self_supply")
                merged_binding = {"package_no": project.package_no, "cob_index": index,
                                  "field": "product_supplier", "value": name, "method": "inferred",
                                  "source": decision["winner_ref"], "reason": decision["reason"]}
                entry["field_binding"] = merged_binding
            audit.append(entry)
        return audit, []
```

## bind_packages — src/ict/bindings.py:29
```python
def bind_packages(understanding, candidates, notice, tables, llm, counter=None,
                  attachment_pages=None, previous=None, package_amounts=None) -> tuple[dict, list[Failure]]:
    known = {package.package_no: package for package in understanding.packages}
    audit = {"protocol_version": "package-binding-v4", "bindings": [], "amount_bindings": [],
             "observation_bindings": [],
             "unresolved": [], "previous": previous or {}}
    unresolved_html_amount = any(amount is not None and amount.amount_yuan is not None and amount.amount_kind == "unknown"
                                 for amount in [understanding.summary_amount] + [p.package_amount for p in understanding.packages])
    # Original-number anchors with no unique directory match must reach the existing model
    # authority. In particular, a same-number lot and contract package cannot be chosen here.
    anchor_rows = [{**anchor, "source_ref": f"html:anchor:{index}"}
                   for index, anchor in enumerate(notice.package_anchors)
                   if anchor.get("amount_yuan") is not None
                   and hint_reference(anchor.get("package_no"), understanding.packages) is None]
    unresolved_html_amount = unresolved_html_amount or bool(anchor_rows)
    if not candidates and not package_amounts and not unresolved_html_amount:
        return audit, []
    # Sole membership is a container fact, not a content inference.
    if len(known) == 1 and not package_amounts and not unresolved_html_amount:
        number = next(iter(known))
        for candidate in candidates:
            if (candidate.package_no in (None, "", number)
                    or hint_reference(candidate.package_no, understanding.packages) == number):
                candidate.binding = {"suggested_package_no": candidate.binding.get("suggested_package_no", candidate.package_no),
                                     "package_no": number, "status": "bound", "method": "single_package"}
                candidate.package_no = number
                candidate.project_id = known[number].project_id
                audit["bindings"].append({"candidate_id": candidate.candidate_id, **candidate.binding})
            else:
                audit["unresolved"].append({"candidate_id": candidate.candidate_id,
                                             "reason": "package_not_in_registry"})
        return audit, []

    source_rows = [row | {"headers": table.headers, "section": table.section,
                          "before_text": table.before_text}
                   for table in notice.tables for row in html_rows(table)]
    source_rows.extend(segment for page in (attachment_pages or [])
                       for segment in page.get("source_segments", []))
    source_rows.extend({"source_ref": f"html:body:{index}", **section}
                       for index, section in enumerate(notice.sections))
    source_rows.extend(anchor_rows)
    references = {row["source_ref"] for row in source_rows}
    amounts = []
    for package in understanding.packages:
        amount = package.package_amount
        if amount is not None:
            amounts.append({"amount_ref": f"amount:package:{package.package_no}",
                            "raw_text": amount.raw_text, "amount_yuan": amount.amount_yuan,
                            "suggested_package_no": package.package_no, "amount_kind": amount.amount_kind,
                            "scope": amount.scope, "source_type": amount.source_type, "source_ref": amount.source_ref,
                            "source_refs": amount.source_refs})
        for index, alternative in enumerate(package.amount_alternatives):
            amounts.append({**alternative.model_dump(), "amount_ref": f"amount:package:{package.package_no}:alternative:{index}",
                            "suggested_package_no": package.package_no})
    if understanding.summary_amount:
        amounts.append({"amount_ref": "amount:summary", **understanding.summary_amount.model_dump()})
    # On later binding passes, a previously adopted anchor is already a package observation.
    represented_sources = {amount.get("source_ref") for amount in amounts if amount.get("source_ref")}
    anchor_amounts = [{"amount_ref": f"amount:{row['source_ref']}",
                       "raw_text": row.get("amount_raw"), "amount_yuan": row["amount_yuan"],
                       "suggested_package_no": row.get("package_no"), "scope": "package",
                       "amount_kind": "unknown", "source_type": "html",
                       "source_ref": row["source_ref"], "source_refs": [row["source_ref"]]}
                      for row in anchor_rows if row["source_ref"] not in represented_sources]
    amounts.extend(anchor_amounts)
    audit["html_anchor_amounts"] = anchor_amounts
    for candidate in candidates:
        if candidate.entity_type != "cob":
            continue
        obs = candidate.fields["total_price"]
        if obs.raw_value in (None, ""):
            continue
        header = (candidate.evidence.column_units if candidate.evidence else {}).get("total_price")
        parsed = parse_price_cell(obs.raw_value, header)
        amounts.append({"amount_ref": f"amount:{candidate.candidate_id}:total_price",
                        "raw_text": str(obs.raw_value), "amount_yuan": parsed["value"],
                        "candidate_id": candidate.candidate_id,
                        "source_class": candidate.source_class, "scope": "object", "amount_kind": "unknown",
                        "source_type": candidate.source.source_type,
                        "source_refs": candidate.evidence.source_refs if candidate.evidence else []})
    attachment_map = {}
    for index, observation in enumerate(package_amounts or []):
        ref = f"amount:attachment:{index}"
        attachment_map[ref] = observation
        amounts.append({**observation.model_dump(), "amount_ref": ref,
                        "suggested_package_no": observation.package_no,
                        "source_class": observation.file_class})
    amount_map = {amount["amount_ref"]: amount for amount in amounts}
    payload = {
        "known_packages": [package.model_dump() for package in understanding.packages],
        "project_name": understanding.project_name,
        "candidates": [{"candidate_id": item.candidate_id, "entity_type": item.entity_type,
                        "suggested_package_no": item.package_no,
                        "fields": {key: obs.normalized_value if obs.normalized_value is not None else obs.raw_value
                                   for key, obs in item.fields.items()},
                        "evidence": None if item.evidence is None else item.evidence.model_dump(),
                        "unmapped_fields": item.unmapped_fields,
                        "previous_binding": item.binding} for item in candidates],
        "source_rows": source_rows, "body_sections": notice.sections,
        "table_contexts": [table.model_dump() for table in tables.tables],
        "amount_observations": amounts,
    }
    ids = {candidate.candidate_id for candidate in candidates}

    def validate_body(parsed):
        bindings = parsed.get("bindings")
        if not isinstance(bindings, list) or any(not isinstance(item, dict) or
                not isinstance(item.get("candidate_id"), str) for item in bindings):
            return "需要 bindings 数组"
        if len(bindings) != len(ids) or {item.get("candidate_id") for item in bindings} != ids:
            return "bindings 必须逐一覆盖全部候选，不得遗漏或重复"
        for item in bindings:
            if item.get("package_no") is not None and (not isinstance(item["package_no"], str) or item["package_no"] not in known):
                return "包号不在输入包集合中，无法判定时填 null"
            refs = item.get("source_refs", [])
            if not isinstance(refs, list) or any(not isinstance(ref, str) or ref not in references for ref in refs):
                return "source_refs 必须引用提供的证据"
            if not isinstance(item.get("reason"), str) or not item["reason"].strip():
                return "每项归属决定需要 reason"
        assignments = parsed.get("amount_bindings")
        if (not isinstance(assignments, list) or len(assignments) != len(known)
                or any(not isinstance(item, dict) or not isinstance(item.get("package_no"), str)
                       for item in assignments)):
            return "amount_bindings 必须逐一覆盖全部包，可用 amount_ref=null 表示未决"
        if {item.get("package_no") for item in assignments if isinstance(item, dict)} != set(known):
            return "amount_bindings 包号不可重复或遗漏"
        chosen = []
        for item in assignments:
            if not isinstance(item.get("reason"), str) or not item["reason"].strip():
                return "每项金额归属决定需要 reason"
            ref = item.get("amount_ref")
            if ref is not None:
                if not isinstance(ref, str) or ref not in amount_map or item.get("amount_kind") != "award":
                    return "包中标金额只允许引用已给出的 award 金额，预算不能供价"
                if amount_map[ref].get("source_class") == "tender_requirement":
                    return "招标需求中的金额不能作为中标金额"
                if amount_map[ref].get("amount_kind") in {"budget", "ceiling"}:
                    return "已明确为预算或限价的观测不能作为中标金额"
                chosen.append(ref)
        if len(chosen) != len(set(chosen)):
            return "同一金额观测不可分配给多个包"
        observations = parsed.get("observation_bindings", [])
        if (not isinstance(observations, list) or any(not isinstance(item, dict) or
                not isinstance(item.get("amount_ref"), str) for item in observations)
                or len(observations) != len(attachment_map)
                or {item.get("amount_ref") for item in observations} != set(attachment_map)):
            return "observation_bindings 必须逐一覆盖附件金额观测"
        for item in observations:
            if item.get("package_no") is not None and (not isinstance(item["package_no"], str) or item["package_no"] not in known):
                return "附件金额归属包号不在公告包集合"
            if (item.get("amount_kind") not in ("award", "budget", "ceiling", "unknown")
                    or not isinstance(item.get("reason"), str) or not item["reason"].strip()):
                return "附件金额归属需要性质与理由"
            original_kind = attachment_map[item["amount_ref"]].amount_kind
            if original_kind in {"budget", "ceiling"} and item["amount_kind"] == "award":
                return "已声明预算或限价不能经归属分配变为成交价"
        by_observation = {item["amount_ref"]: item for item in observations}
        for item in assignments:
            observed = by_observation.get(item.get("amount_ref"))
            if observed and (observed["package_no"] != item["package_no"] or observed["amount_kind"] != "award"):
                return "包供值必须与附件观测归属及成交性质一致"
        return ""

    try:
        parsed = complete_json(llm, step="bind_packages", prompt_version="package-binding-v4",
                               user=json.dumps(payload, ensure_ascii=False), validate=validate_body, counter=counter)
    except (LLMError, ValueError) as exc:
        audit["fallback"] = str(exc)
        audit["unresolved"] = [{"candidate_id": item.candidate_id, "reason": "binding_model_failed"}
                                for item in candidates]
        for ref, observation in attachment_map.items():
            observation.binding = {"amount_ref": ref, "status": "unresolved", "reason": "binding_model_failed"}
            audit["unresolved"].append(observation.binding)
        code = ("llm_call_failed" if isinstance(exc, LLMError) else "llm_validator_incompatible"
                if isinstance(exc, LLMValidationError) and any(issue.category == "implementation" for issue in exc.issues)
                else "llm_schema_invalid")
        return audit, [Failure(failure_code=code, failure_message=str(exc),
                               location="bind_packages")]
    by_id = {candidate.candidate_id: candidate for candidate in candidates}
    for binding in parsed["bindings"]:
        candidate = by_id[binding["candidate_id"]]
        number = binding["package_no"]
        candidate.binding = {"suggested_package_no": candidate.binding.get("suggested_package_no", candidate.package_no),
                             **binding, "status": "bound" if number else "unresolved", "method": "model"}
        candidate.package_no = number
        candidate.project_id = None if number is None else make_project_id(understanding.project_name, number)
        audit["bindings"].append(candidate.binding)
        if number is None:
            audit["unresolved"].append(binding)
    for binding in parsed["amount_bindings"]:
        package = known[binding["package_no"]]
        source = amount_map.get(binding.get("amount_ref"))
        package.package_amount = None if source is None else Amount(
            raw_text=source["raw_text"], amount_yuan=source.get("amount_yuan"), scope="package",
            amount_kind="award", source_type=source.get("source_type", "html"), source_ref=source.get("source_ref"),
            source_refs=source.get("source_refs", []) or ([source["source_ref"]] if source.get("source_ref") else []),
            binding=binding)
        audit["amount_bindings"].append(binding)
    for binding in parsed.get("observation_bindings", []):
        observation = attachment_map[binding["amount_ref"]]
        observation.binding = {"suggested_package_no": observation.binding.get("suggested_package_no", observation.package_no),
                               **binding, "status": "bound" if binding["package_no"] else "unresolved"}
        if binding["package_no"]:
            observation.package_no = binding["package_no"]
        observation.amount_kind = binding["amount_kind"]
        audit["observation_bindings"].append(observation.binding)
        if binding["package_no"] is None:
            audit["unresolved"].append(observation.binding)
    return audit, []
```

## merge_subs — src/ict/sub_merge.py:9
```python
def merge_subs(items, package_no, llm, counter, value_of, package_metadata=None, source_contexts=None):
    rows = [item for item in items if item.entity_type == "sub"]
    if llm is None or len(rows) < 2:
        return None
    by_id = {item.candidate_id: item for item in rows}
    payload = {"package_no": package_no, "package_registry": [package_metadata] if package_metadata else [], "candidates": [
        {"candidate_id": item.candidate_id,
         "fields": {key: value_of(item, key) for key in
                    SUB_FIELDS},
         "evidence": item.evidence.model_dump() if item.evidence else None,
         "unmapped_fields": item.unmapped_fields} for item in rows]}
    refs = {ref for item in rows if item.evidence for ref in item.evidence.source_refs}
    payload["source_contexts"] = {ref: context for ref, context in (source_contexts or {}).items() if ref in refs}

    def validate(parsed):
        groups, excluded = parsed.get("groups"), parsed.get("excluded", [])
        if not isinstance(groups, list) or not isinstance(excluded, list):
            return "需要 groups、excluded 数组"
        seen = []
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("candidate_ids"), list) or not group["candidate_ids"]:
                return "每组需要 candidate_ids"
            ids = group["candidate_ids"]
            if any(not isinstance(cid, str) or cid not in by_id for cid in ids):
                return ValidationIssue("SUB 组引用未知候选", "plan_constraint")
            seen.extend(ids)
            if group.get("name_from") not in ids or not value_of(by_id[group["name_from"]], "supplier_name"):
                return ValidationIssue("name_from 必须引用本组有名称的候选", "plan_constraint")
            if not isinstance(group.get("winner"), bool) or not isinstance(group.get("reason"), str) or not group["reason"].strip():
                return "每组需要 winner 布尔值和 reason"
            for key in ("score_from", "amount_from", "bid_amount_from", "review_from", "review_reason_from"):
                if group.get(key) is not None and (not isinstance(group[key], str) or group[key] not in ids):
                    return ValidationIssue(f"{key} 必须引用本组候选", "plan_constraint")
            review_source = group.get("review_from")
            review = value_of(by_id[review_source], "review_status") if review_source else "unknown"
            if review not in (None, "", "unknown", "passed", "failed"):
                return ValidationIssue("审查状态不可解析", "plan_constraint")
            if review == "failed" and group["winner"]:
                return ValidationIssue("未通过审查不能同时确认中标，需结合证据解决冲突", "business")
            if group.get("review_reason_from") and review != "failed":
                return ValidationIssue("未通过原因只能跟随 failed 结论", "plan_constraint")
            # Every populated new field requires either a source or an explicit unresolved note.
            unresolved_fields = group.get("unresolved_fields", [])
            if not isinstance(unresolved_fields, list) or any(field not in SUB_FIELDS for field in unresolved_fields):
                return "unresolved_fields 必须为业务字段名数组"
            for field, selector in (("bid_amount", "bid_amount_from"), ("review_status", "review_from"),
                                    ("review_failure_reason", "review_reason_from")):
                selected = group.get(selector)
                if selected and value_of(by_id[selected], field) in (None, "", "unknown"):
                    return ValidationIssue(f"{selector} 指向不可用观测", "plan_constraint")
                if any(value_of(by_id[cid], field) not in (None, "", "unknown") for cid in ids):
                    if group.get(selector) is None and field not in unresolved_fields:
                        return ValidationIssue(f"{field} 已有观测，必须选来源或明确未决", "plan_constraint")
        for entry in excluded:
            if (not isinstance(entry, dict) or not isinstance(entry.get("candidate_id"), str)
                    or entry["candidate_id"] not in by_id or not isinstance(entry.get("reason"), str)
                    or not entry["reason"].strip()):
                return "excluded 需要有效 candidate_id 与 reason"
            if value_of(by_id[entry["candidate_id"]], "review_status") == "failed":
                return ValidationIssue("未通过投标主体必须保留在供应商分组", "plan_constraint")
            seen.append(entry["candidate_id"])
        if len(seen) != len(by_id) or set(seen) != set(by_id):
            return ValidationIssue("SUB 计划必须无重复且完整覆盖候选", "plan_constraint")
        return ""

    try:
        answer = complete_json(llm, step="merge_subs", prompt_version="merge-subs-v2",
                               user=json.dumps(payload, ensure_ascii=False), validate=validate, counter=counter)
    except (ValueError, LLMError) as exc:
        return {"fallback": str(exc), "attempts": getattr(exc, "attempts", []), "accepted": False}
    output, source_ids, unresolved = [], [], []
    for group in answer["groups"]:
        values = {}
        for field, selector in (("supplier_name", "name_from"), ("score", "score_from"),
                                ("supplier_award_amount", "amount_from"), ("bid_amount", "bid_amount_from"),
                                ("review_status", "review_from"), ("review_failure_reason", "review_reason_from")):
            source = group.get(selector)
            values[field] = value_of(by_id[source], field) if source else None
            if source and values[field] in (None, ""):
                unresolved.append({"package_no": package_no, "candidate_id": source,
                                   "field": field, "reason": "sub_selected_field_unavailable"})
        values["review_status"] = values["review_status"] or "unknown"
        for field in group.get("unresolved_fields", []):
            unresolved.append({"package_no": package_no, "field": field, "reason": group["reason"],
                               "candidate_ids": group["candidate_ids"]})
        output.append(Sub(**values, is_winner=group["winner"]))
        source_ids.append(group["name_from"])
    return {"accepted": True, "subs": output, "source_ids": source_ids,
            "unresolved": unresolved, "groups": answer["groups"], "excluded": answer.get("excluded", [])}
```

## screen_files — src/ict/parse/screen.py:155
```python
def _ask_text(llm, entry: FileEntry, peek: Peek, counter, needs: list[dict] | None = None) -> tuple[dict, int]:
    payload = {"file_name": entry.name, "format": entry.fmt, "pages": entry.pages,
               "table_headers": peek.table_headers[:3], "needs": needs or [],
               "content_view": peek.view[:SCREEN_TEXT_CHARS]}
    payload["source_segments"] = peek.segments
    parsed = complete_json(
        llm, step="screen_files", prompt_version="screen-file-v4",
        user=json.dumps(payload, ensure_ascii=False),
        validate=lambda value: _validate(value) or (
            "evidence_refs 必须引用给出的 source_segments" if any(
                not isinstance(ref, str) or ref not in {segment["source_ref"] for segment in peek.segments}
                for ref in value.get("evidence_refs", [])) else "") or _validate_need_ids(value, needs), counter=counter)
    return parsed, 0
```

## screen_images — src/ict/parse/screen.py:170
```python
def _ask_images(llm, entry: FileEntry, images: list[str], counter, needs: list[dict] | None = None) -> tuple[dict, int]:
    if llm is None:
        return {}, 0
    from ict.llm import parse_json_object

    try:
        result = llm.complete_with_images(step="screen_images", prompt_version="screen-page-v4",
                                          images=images, user_text=json.dumps({"file_name": entry.name,
                                                                               "needs": needs or []}, ensure_ascii=False))
    except Exception:  # noqa: BLE001 - no vision on this model, or the call failed: keep the file
        return {}, 0
    try:
        parsed = parse_json_object((result.text or "").strip())
        if _validate(parsed) or _validate_need_ids(parsed, needs):
            return {}, 0
        return parsed, 0
    except Exception:  # noqa: BLE001 - unparsable gate keeps the file, never drops it
        return {}, 0
```