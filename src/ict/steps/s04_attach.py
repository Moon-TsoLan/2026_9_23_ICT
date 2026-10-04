"""Steps 4, 4b, 5 and 6 for attachments.

4   screen    census data plus a native text view, then a model gate, per file
4b  parse     one page-parsing call per selected file, kept in memory
5   locate    page selection inside the already parsed pages, with real page numbers
6   extract   entity candidates from the selected pages, one call per package scope

The old reader that looked for a pre-converted markdown twin is gone, so page numbers,
table structure and scanned content now come from the same source for every format.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ict.candidates import seal_candidate
from ict.config import (
    ATTACHMENTS_ROOT,
    CLASS_PRIORITY,
    DEFAULT_FILE_CLASS,
    DEFAULT_SOURCE_PRIORITY,
    FILE_CLASSES,
    MAX_PAGES_PER_ANNOUNCEMENT,
    MAX_PAGES_PER_FILE,
    PAGE_CONTEXT_CHARS,
    PAGE_CONTEXT_TABLES,
    PAGE_LOCATE_LIMIT,
    PAGE_TEXT_HEAD,
    PARSE_MAX_PAGES_PER_FILE,
    PARSE_PAGE_PAD,
    PARSE_RUN_PAGES,
    parse_settings,
)
from ict.documents import read_native
from ict.ids import make_project_id
from ict.llm import LLMClient, LLMError, complete_json
from ict.parse.client import ParseClient, ParseError, as_pages, contiguous_runs
from ict.parse.peek import page_profiles, peek_entry
from ict.parse.screen import ScreenDecision, gate_file, name_rule
from ict.schemas import (
    AttachmentIndex,
    Candidate,
    Failure,
    FileDecision,
    FileDecisions,
    IndexedFile,
    PageDecision,
    PageDecisions,
    ProjectPlans,
)

FILE_PACKAGE_RE = re.compile(r"(?:标\s*包|采购包|合同包|第)?\s*([0-9]+|[A-Za-z])\s*包|(?:标\s*包|采购包|合同包)\s*[:：]?\s*([0-9]+|[A-Za-z])")
PAGE_PACKAGE_RE = re.compile(r"第\s*([0-9]+)\s*包|(?:标\s*包|采购包|合同包|包号|包)\s*[:：]?\s*([0-9]+|[A-Za-z])(?![0-9A-Za-z])")

KIND_TO_CLASS = {
    "award_detail": "award_detail",
    "bid_quote": "bid_quote",
    "winner_detail": "winner_detail",
    "tender_requirement": "tender_requirement",
    "qualification": "qualification",
    "contract": "contract",
    "evaluation": "evaluation",
    "other": "unrelated",
    "unknown": "unknown",
}
PARSE_KINDS = {"award_detail", "bid_quote", "winner_detail"}
PARSE_METHOD_FORCED = "forced"
def page_packages(text: str, known: list[str]) -> set[str]:
    known_set = set(known)
    found = set()
    for match in PAGE_PACKAGE_RE.finditer(text or ""):
        found.add(next(group for group in match.groups() if group))
    return found & known_set


def coerce_package_scope(scope: str, possible: list[str], known: list[str], file_name: str) -> str:
    """Keep a page on a package the announcement actually has."""
    known_set = set(known)
    possible_known = list(dict.fromkeys(package for package in possible if package in known_set))
    filename_package = _package_from_filename(file_name or "")
    if filename_package not in known_set:
        filename_package = None
    if scope in known_set or scope == "announcement":
        return scope
    if len(possible_known) == 1:
        return possible_known[0]
    if filename_package and not possible_known:
        return filename_package
    if scope not in known_set and len(known_set) == 1 and scope not in {"", "unknown"}:
        return next(iter(known_set))
    return "unknown"


def _package_from_filename(file_name: str) -> str | None:
    found = []
    for match in FILE_PACKAGE_RE.finditer(file_name):
        package_no = next(group for group in match.groups() if group)
        if package_no not in found:
            found.append(package_no)
    if len(found) == 1:
        return found[0]
    return None


def _entry(index: AttachmentIndex, item: IndexedFile):
    from ict.parse.census import FileEntry

    root = Path(index.attachment_directory or (ATTACHMENTS_ROOT / index.announcement_id))
    path = root / item.relative_path
    return FileEntry(file_id=item.file_id, name=item.display_name, relative_path=item.relative_path,
                     path=path, size_bytes=path.stat().st_size if path.exists() else 0,
                     declared_ext=item.extension, fmt=item.fmt, note=item.note, digest=item.digest,
                     pages=item.page_count)


def _client() -> ParseClient:
    settings = parse_settings()
    return ParseClient(settings["url"], settings.get("token", ""), float(settings.get("timeout", 600)))


def triage_files(run_id: str, plans: ProjectPlans, index: AttachmentIndex | None, llm: LLMClient | None,
                 counter=None) -> FileDecisions:
    """Step 4. Screen every file; nothing is parsed before a file is kept."""
    failures: list[Failure] = []
    if index is None or index.attachment_directory is None:
        failures.append(Failure(failure_code="attachment_index_miss", failure_message="附件索引不存在"))
        return FileDecisions(run_id=run_id, status="skipped", failures=failures)
    if not any(project.needs_attachment for project in plans.projects):
        return FileDecisions(run_id=run_id, status="skipped", failures=failures)

    needs = [entry for project in plans.projects for entry in (project.needs or [])]
    package_set = {project.package_no for project in plans.projects}
    decisions: list[FileDecision] = []
    for item in index.files:
        entry = _entry(index, item)
        if not entry.path.exists():
            decisions.append(FileDecision(file_id=item.file_id, file_class="unknown", priority=0,
                                          read_strategy="unsupported", reason="file_missing",
                                          failure_code="document_parse_failed", failure_message="附件文件不存在"))
            failures.append(Failure(failure_code="document_parse_failed", failure_message=item.display_name,
                                    location=item.file_id))
            continue
        peek = peek_entry(entry)
        decision: ScreenDecision = gate_file(entry, peek, llm, counter=counter, needs=needs)
        file_class = KIND_TO_CLASS.get(decision.kind, "unknown")
        if file_class not in FILE_CLASSES:
            file_class = "unknown"
        keep = decision.decision == "parse"
        # Two facts, neither one a judgement of its own. expected_fields is exactly what the model
        # said it saw in this file. The package hint is the single number the filename names, kept
        # only when the announcement really has that package. Both feed inputs that already exist
        # downstream; no new branch decides anything.
        hint = _package_from_filename(entry.name)
        possible = [hint] if hint and hint in package_set else []
        decisions.append(
            FileDecision(
                file_id=item.file_id,
                file_class=file_class,
                expected_fields=list(decision.found_fields),
                possible_packages=possible,
                priority=float(CLASS_PRIORITY.get(file_class, 30)) * max(decision.confidence, 0.3),
                read_strategy="target_pages" if keep else "skip",
                reason="%s:%s:%s" % (decision.method, decision.reason or decision.kind, "%.2f" % decision.confidence),
                screen=decision.short() | {"name_rule": decision.name_rule, "format": entry.fmt,
                                           "pages": entry.pages, "view_chars": peek.full_chars},
                quote_supplier=decision.quote_supplier or None,
                is_award_notice=decision.is_award_notice,
            )
        )
    # No file-count cap (user instruction 2026-10-03): everything the gate keeps is parsed.
    # Cost is bounded per page instead, in parse_pages and in the step 5 limits.
    decisions.sort(key=lambda item: (item.read_strategy != "target_pages", -item.priority))
    status = "partial" if failures else "success"
    return FileDecisions(run_id=run_id, status=status, file_decisions=decisions, failures=failures)


def _budget_pages(entry, cap: int, failures: list[Failure]) -> tuple[list[int] | None, list[dict]]:
    """Which pages to parse when the file is longer than the budget.

    Returns (pages, profiles); pages is None to mean "the front of the file, as before" - which is
    what happens for short files, for non-PDF containers with no native text, for a profile that
    could not be read, and whenever the ranking lands on the same front pages anyway. Nothing here
    judges content value: it ranks two structural counts and keeps page order as the tie-break.
    """
    if entry.fmt != "pdf" or not (entry.pages or 0) > cap:
        return None, []
    try:
        profiles = page_profiles(entry.path)
    except Exception as exc:  # noqa: BLE001 - a bad profile must not cost the file its parse
        failures.append(Failure(failure_code="document_parse_failed",
                                failure_message="page_profile:" + type(exc).__name__,
                                location=entry.file_id))
        return None, []
    if not profiles:
        return None, []
    chosen = rank_budget(profiles, cap)
    if chosen == list(range(1, cap + 1)):
        return None, profiles
    return chosen, profiles


def rank_budget(profiles: list[dict], cap: int) -> list[int]:
    """The pure half of `_budget_pages`: order pages by two structural counts, keep page order as
    the tie-break, take the budget. Sorted ascending on the way out so the request is stable."""
    ranked = sorted(profiles, key=lambda item: (-(1 if item["tables"] else 0),
                                                 -item["price_hits"], item["page_no"]))
    return sorted(item["page_no"] for item in ranked[:cap])


def parse_pages(run_id: str, files: FileDecisions, index: AttachmentIndex, failures: list[Failure]):
    """Step 4b. One parsing call per kept file; pages stay in memory for steps 5 and 6."""
    chosen = [item for item in files.file_decisions if item.read_strategy == "target_pages"]
    parsed: dict[str, list[dict]] = {}
    summary: list[dict] = []
    if not chosen:
        return parsed, summary
    client = _client()
    for decision in chosen:
        item = next((entry for entry in index.files if entry.file_id == decision.file_id), None)
        if item is None:
            continue
        entry = _entry(index, item)
        profiles: list[dict] = []
        try:
            if entry.fmt in {"docx", "xlsx", "xlsm"} and not entry.needs_normalisation:
                pages = read_native(entry.path)
            else:
                cap = min(entry.pages or PARSE_MAX_PAGES_PER_FILE, PARSE_MAX_PAGES_PER_FILE)
                wanted, profiles = _budget_pages(entry, cap, failures)
                response = client.parse(entry.path, pages=wanted, max_pages=cap)
                pages = as_pages(response)
                decision.parse_meta = (response.get("meta") or {})
        except ParseError as exc:
            code = "parse_unreachable" if str(exc.reason).startswith("parse_unreachable") else "parse_http_failed"
            failures.append(Failure(failure_code=code, failure_message=str(exc.reason)[:200], location=decision.file_id))
            decision.read_strategy = "skip"
            decision.reason = "parse_failed:" + str(exc.reason)[:80]
            continue
        except Exception as exc:  # noqa: BLE001 - one unreadable file must not stop the run
            failures.append(Failure(failure_code="document_parse_failed", failure_message=type(exc).__name__ + ":" + str(exc)[:120],
                                    location=decision.file_id))
            decision.read_strategy = "skip"
            decision.reason = "parse_failed:" + type(exc).__name__
            continue
        parsed[decision.file_id] = pages
        record = {"file_id": decision.file_id, "name": entry.name, "pages": len(pages),
                  "chars": sum(page["chars"] for page in pages),
                  "tables": sum(len(page["tables"]) for page in pages),
                  "table_headers": {page["page_no"]: [table["headers"] for table in page["tables"]][:1]
                                    for page in pages if page["tables"]}}
        if profiles:
            # The truncation used to be silent: a 752-page bundle was cut at 40 and nobody recorded
            # that pages 41 onwards held the actual detail table.
            reached = {page["page_no"] for page in pages}
            record["pages_total"] = entry.pages
            record["budget"] = cap
            record["table_pages_missed"] = sum(1 for item in profiles
                                                if item["tables"] and item["page_no"] not in reached)
            record["price_pages_missed"] = sum(1 for item in profiles
                                               if item["price_hits"] and item["page_no"] not in reached)
        summary.append(record)
    return parsed, summary


def _validate_pages(parsed: dict) -> str:
    if not isinstance(parsed.get("page_decisions"), list):
        return "需要 page_decisions 数组"
    for item in parsed["page_decisions"]:
        if item.get("extraction_mode") not in {None, "", "text", "table", "text_and_table", "unsupported"}:
            return "extraction_mode 不在允许值内"
    return ""


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
                            "found_fields": decision.expected_fields, "pages": slim[:PAGE_LOCATE_LIMIT]})
    try:
        produced = complete_json(
            llm, step="locate_pages", prompt_version="locate-pages-v1",
            user=json.dumps({"known_packages": known_packages,
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
            mentioned[(file_id, page["page_no"])] = page_packages(page["text"], known_packages)
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
                                     name_by_file.get(file_id, ""))
        if len(mentioned.get((file_id, page_no), set())) >= 2:
            scope = "announcement"
        decisions.append(PageDecision(file_id=file_id, page_no=page_no,
                                      relevance=float(item.get("relevance") or 0),
                                      expected_fields=item.get("expected_fields") or [],
                                      package_scope=scope, extraction_mode=mode,
                                      reason=item.get("reason") or ""))
    status = "partial" if not decisions else "success"
    return PageDecisions(run_id=run_id, status=status, page_decisions=decisions)


def _validate_candidates(parsed: dict) -> str:
    if not isinstance(parsed.get("candidates"), list):
        return "需要 candidates 数组"
    amounts = parsed.get("package_amounts")
    if amounts is not None and not isinstance(amounts, list):
        return "package_amounts 必须是数组"
    for item in amounts or []:
        if not isinstance(item, dict) or not str(item.get("package_no") or "") or not str(item.get("raw_text") or ""):
            return "package_amounts 每项需要 package_no 与 raw_text"
    return ""


def file_names_of(index: AttachmentIndex | None) -> dict[str, str]:
    return {item.file_id: item.display_name for item in (index.files if index else [])}


def extract_attachment_candidates(run_id: str, project_name: str, plans: ProjectPlans, pages: PageDecisions,
                                 parsed: dict[str, list[dict]], llm: LLMClient | None, seq_start: int,
                                 counter=None, file_classes: dict[str, str] | None = None,
                                 failures: list[Failure] | None = None,
                                 file_names: dict[str, str] | None = None,
                                file_found_fields: dict[str, list[str]] | None = None,
                                package_amounts: list | None = None,
                                file_quote_suppliers: dict[str, str] | None = None,
                                winner_suppliers: dict[str, list[str]] | None = None,
                                file_award_notices: dict[str, bool] | None = None):
    """Step 6. Extract from parsed pages, one call per package scope, chunked not truncated."""
    failures = failures if failures is not None else []
    candidates: list[Candidate] = []
    quality: list[dict] = []
    seq = seq_start
    if not pages.page_decisions or llm is None:
        if not pages.page_decisions:
            return candidates, failures, quality, seq
        failures.append(Failure(failure_code="llm_call_failed", failure_message="未配置模型"))
        return candidates, failures, quality, seq

    by_package: dict[str, list[dict]] = {}
    for decision in pages.page_decisions:
        if decision.extraction_mode == "unsupported":
            continue
        page_list = parsed.get(decision.file_id) or []
        page = next((item for item in page_list if item["page_no"] == decision.page_no), None)
        if page is None:
            quality.append({"file_id": decision.file_id, "page_no": decision.page_no,
                            "readability": "missing_page", "failure_code": "unexpected_error"})
            continue
        quality.append({"file_id": decision.file_id, "page_no": decision.page_no,
                        "readability": "text_extractable", "failure_code": None,
                        "chars": page["chars"], "tables": len(page["tables"])})
        by_package.setdefault(decision.package_scope, []).append({
            "file_id": decision.file_id,
            "file_name": (file_names or {}).get(decision.file_id, decision.file_id),
            "page_no": page["page_no"],
            "text": page["text"][:PAGE_CONTEXT_CHARS],
            "tables": page["tables"][:PAGE_CONTEXT_TABLES],
            "found_fields": (file_found_fields or {}).get(decision.file_id, []),
            "source_type": {"vl": "pdf", "docx": "docx", "xlsx": "xlsx", "pdf": "pdf"}.get(page.get("source"), "pdf"),
            "quote_supplier": (file_quote_suppliers or {}).get(decision.file_id),
        })

    plans_by_no = {project.package_no: project for project in plans.projects}
    for package_no, contexts in by_package.items():
        plan = plans_by_no.get(package_no)
        for chunk_start in range(0, len(contexts), PARSE_RUN_PAGES):
            chunk = contexts[chunk_start:chunk_start + PARSE_RUN_PAGES]
            if chunk_start + PARSE_RUN_PAGES < len(contexts):
                failures.append(Failure(failure_code="page_context_split",
                                        failure_message="包 %s 页数 %d，拆成多次调用" % (package_no, len(contexts)),
                                        location=str(package_no)))
            payload = {"current_package": {
                "project_id": None if plan is None else plan.project_id,
                "package_no": None if package_no in {"unknown", "announcement"} else package_no,
                "missing_fields": [] if plan is None else plan.missing_fields,
                "needs": [] if plan is None else (plan.needs or []),
                # Already established upstream, so step 6 copies it instead of re-deriving it.
                # Empty means nobody knows yet and the page is the only place left to look.
                "winner_supplier": _known_winner(package_no, chunk, winner_suppliers,
                                                 file_quote_suppliers, file_award_notices)},
                "page_contexts": chunk}
            try:
                produced = complete_json(
                    llm, step="extract_attachment_candidates", prompt_version="attachment-extract-v1",
                    user=json.dumps(payload, ensure_ascii=False), validate=_validate_candidates, counter=counter)
            except (LLMError, ValueError) as exc:
                code = "llm_call_failed" if isinstance(exc, LLMError) else "llm_schema_invalid"
                failures.append(Failure(failure_code=code, failure_message=str(exc)[:200], location=package_no))
                continue
            for entry in produced.get("candidates") or []:
                entity = entry.get("entity_type")
                if entity not in {"cob", "sub"}:
                    continue
                file_id = entry.get("file_id") or (chunk[0]["file_id"] if chunk else None)
                context = next((item for item in chunk if item["file_id"] == file_id), chunk[0] if chunk else {})
                file_class = (file_classes or {}).get(file_id or "", DEFAULT_FILE_CLASS)
                item_package = entry.get("package_no") or payload["current_package"]["package_no"] or \
                    _package_from_filename(context.get("file_name", ""))
                project_id = make_project_id(project_name, str(item_package)) if item_package and project_name else None
                candidates.append(seal_candidate(
                    candidate_id="cand_%06d" % seq, entity_type=entity, project_id=project_id,
                    package_no=None if item_package is None else str(item_package),
                    source_type=context.get("source_type", "pdf"), file_id=file_id,
                    source_priority=int(CLASS_PRIORITY.get(file_class, DEFAULT_SOURCE_PRIORITY)),
                    raw_fields=entry.get("fields") or {}, issues=entry.get("issues") or [],
                    source_class=file_class, page_no=context.get("page_no"),
                    table_index=_int_or_none(entry.get("table_index")),
                    row_text=_row_text_of(entry),
                    quote_supplier=entry.get("quote_supplier") or context.get("quote_supplier"),
                    bidder_supplier=entry.get("bidder_supplier"),
                    winner_supplier=entry.get("winner_supplier")
                    or payload["current_package"]["winner_supplier"]))
                seq += 1
            if package_amounts is not None:
                package_amounts.extend(_package_amount_records(
                    produced.get("package_amounts"), chunk, package_no, file_classes, file_names))
    return candidates, failures, quality, seq


def _known_winner(package_no: str, chunk: list[dict], winner_suppliers: dict[str, list[str]] | None,
                  file_quote_suppliers: dict[str, str] | None,
                  file_award_notices: dict[str, bool] | None) -> str | None:
    """The winner step 6 should copy rather than look for.

    Two upstream sources, in order: the award text of the announcement, then a document the screen
    gate read as an award notice - such a document names the winner by definition, so its supplier
    is already known and re-deriving it from the page would only risk a different spelling.
    """
    known = (winner_suppliers or {}).get(package_no) or []
    if known:
        return known[0]
    for context in chunk:
        file_id = context.get("file_id")
        if (file_award_notices or {}).get(file_id):
            owner = (file_quote_suppliers or {}).get(file_id)
            if owner:
                return owner
    return None


def _int_or_none(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _row_text_of(entry: dict) -> str | None:
    """The line a candidate came from. The model echoes it; a list is joined, a string is kept."""
    row = entry.get("row_text")
    if isinstance(row, (list, tuple)):
        row = " | ".join(str(cell) for cell in row if str(cell).strip())
    return None if row in (None, "") else str(row)


def _package_amount_records(items, chunk: list[dict], group_package: str,
                            file_classes: dict[str, str] | None, file_names: dict[str, str] | None) -> list:
    """Keep only what the page wrote in black and white: a package number and an amount string.

    An entry without a package number or without text is dropped. Everything else is kept as the
    page wrote it - a package number the announcement does not have stays in the output for audit,
    and step 8 simply never looks it up. An amount string that will not parse stays with
    amount_yuan=None; nothing here guesses a number the page did not print.
    """
    from ict.money import parse_amount
    from ict.schemas import PackageAmountObservation

    out: list = []
    seen: set[tuple] = set()
    for item in items or []:
        if not isinstance(item, dict):
            continue
        package_no = str(item.get("package_no") or group_package or "").strip()
        raw_text = str(item.get("raw_text") or "").strip()
        if not package_no or not raw_text:
            continue
        context = next((entry for entry in chunk if entry["file_id"] == item.get("file_id")),
                       chunk[0] if chunk else {})
        value, _ = parse_amount(raw_text)
        key = (package_no, raw_text, context.get("file_id"), context.get("page_no"))
        if key in seen:
            continue
        seen.add(key)
        file_id = context.get("file_id")
        out.append(PackageAmountObservation(
            package_no=package_no, raw_text=raw_text, amount_yuan=value,
            source_type=context.get("source_type", "pdf"), file_id=file_id,
            file_name=(file_names or {}).get(file_id or "", ""),
            page_no=context.get("page_no"),
            file_class=(file_classes or {}).get(file_id or "", DEFAULT_FILE_CLASS)))
    return out
