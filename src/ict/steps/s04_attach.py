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
import time
from concurrent.futures import CancelledError
from pathlib import Path

from ict.candidates import seal_candidate
from ict.packages import package_context, hint_reference, reference_matches
from ict.concurrency import PARSE_QUEUE, parallel_map
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
from ict.evidence import page_segments, resolve_context
from ict.llm import LLMClient, LLMError, complete_json
from ict.parse.client import ParseClient, ParseError, as_pages, contiguous_runs
from ict.parse.peek import page_profiles, peek_entry
from ict.parse.screen import ScreenDecision, gate_file, name_rule
from ict.parse.census import ARCHIVE_FORMATS
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
def page_packages(text: str, known: list[str], package_registry=None) -> set[str]:
    known_set = set(known)
    found = set()
    for match in PAGE_PACKAGE_RE.finditer(text or ""):
        found.add(next(group for group in match.groups() if group))
    if package_registry is None:
        return found & known_set
    return {ref for number in found for ref in reference_matches(number, package_registry)} & known_set


def coerce_package_scope(scope: str, possible: list[str], known: list[str], file_name: str,
                         package_registry=None) -> str:
    """Keep a page on a package the announcement actually has."""
    known_set = set(known)
    possible_known = list(dict.fromkeys(package for package in possible if package in known_set))
    filename_package = _package_from_filename(file_name or "")
    if package_registry is not None:
        filename_package = hint_reference(filename_package, package_registry)
        if scope not in known_set and scope not in {"announcement", "unknown", ""}:
            matches = reference_matches(scope, package_registry)
            if len(matches) > 1:
                return "announcement"
            if len(matches) == 1:
                scope = matches[0]
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
    return ParseClient(settings["url"], settings.get("token", ""),
                       float(settings.get("timeout", 600)),
                       float(settings.get("connect_timeout", 10)))


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

    def screen_one(item: IndexedFile) -> tuple[FileDecision, Failure | None]:
        """One file in, one keep-or-drop decision out. Reads nothing another file owns."""
        entry = _entry(index, item)
        if not entry.path.exists():
            return (FileDecision(file_id=item.file_id, file_class="unknown", priority=0,
                                 read_strategy="unsupported", reason="file_missing",
                                 failure_code="document_parse_failed", failure_message="附件文件不存在"),
                    Failure(failure_code="document_parse_failed", failure_message=item.display_name,
                            location=item.file_id))
        if entry.fmt in ARCHIVE_FORMATS:
            # An unexpanded container has no pages for the OCR endpoint. Keep its failure,
            # not an upload job; successfully expanded members are independently indexed.
            return (FileDecision(file_id=item.file_id, file_class="unknown", priority=0,
                                 read_strategy="unsupported", reason="archive_not_expanded:" + entry.fmt,
                                 failure_code="document_parse_failed",
                                 failure_message="归档未完整展开，不能提交页解析接口"),
                    Failure(failure_code="document_parse_failed",
                            failure_message="archive_not_expanded:" + entry.name, location=item.file_id))
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
        hint = hint_reference(_package_from_filename(entry.name), plans.projects)
        possible = [hint] if hint and hint in package_set else []
        return (
            FileDecision(
                file_id=item.file_id,
                needs_hit_ids=decision.needs_hit_ids,
                file_class=file_class,
                expected_fields=list(decision.found_fields),
                possible_packages=possible,
                priority=float(CLASS_PRIORITY.get(file_class, 30)) * max(decision.confidence, 0.3),
                read_strategy="target_pages" if keep else "skip",
                reason="%s:%s:%s" % (decision.method, decision.reason or decision.kind, "%.2f" % decision.confidence),
                screen=decision.short() | {"name_rule": decision.name_rule, "format": entry.fmt,
                                           "pages": entry.pages, "view_chars": len(peek.view),
                                           "native_full_chars": peek.full_chars,
                                           "view_is_truncated": sum(len(s["text"]) for s in peek.segments) < peek.full_chars},
                quote_supplier=decision.quote_supplier or None,
                is_award_notice=decision.is_award_notice,
                native_segments=peek.segments,
                evidence_refs=decision.evidence_refs,
            ),
            None,
        )

    # One independent call per file. Decisions come back in index order, so the sort below and the
    # file id sequence read exactly as the serial loop wrote them.
    for decision, failure in parallel_map(screen_one, index.files):
        decisions.append(decision)
        if failure is not None:
            failures.append(failure)
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


def parse_pages(run_id: str, files: FileDecisions, index: AttachmentIndex, failures: list[Failure], *, progress=None, owner=None):
    """Step 4c. Read files still requiring depth; pages stay in memory for steps 5 and 6.

    Remote requests use the shared bounded queue, including upload and response waiting. The
    files are still collected in the order they were chosen, so the summary and the failure list
    read exactly as the serial loop wrote them.
    """
    chosen = [item for item in files.file_decisions if item.read_strategy == "target_pages"]
    parsed: dict[str, list[dict]] = {}
    summary: list[dict] = []
    if not chosen:
        return parsed, summary
    client = _client()

    def emit(decision, event):
        if progress is not None:
            progress({"file_id": decision.file_id, "time": time.time(), **event})

    def submit_parse(decision, entry, wanted, cap):
        submitted = time.perf_counter()
        emit(decision, {"event": "queued", "name": entry.name, "file_bytes": entry.size_bytes})

        def invoke(*, cancel):
            emit(decision, {"event": "dequeued", "queue_s": time.perf_counter() - submitted})
            return client.parse(entry.path, pages=wanted, max_pages=cap, cancel=cancel,
                                progress=lambda event: emit(decision, event))
        return PARSE_QUEUE.submit_cancellable(owner or run_id, invoke)

    jobs: list[dict] = []
    for decision in chosen:
        item = next((entry for entry in index.files if entry.file_id == decision.file_id), None)
        if item is None:
            continue
        entry = _entry(index, item)
        if entry.fmt in ARCHIVE_FORMATS:
            decision.read_strategy = "unsupported"
            decision.reason = "archive_not_expanded:" + entry.fmt
            failures.append(Failure(failure_code="document_parse_failed", location=entry.file_id,
                                    failure_message=decision.reason))
            continue
        if entry.fmt in {"docx", "xlsx", "xlsm", "text"} and not entry.needs_normalisation:
            jobs.append({"decision": decision, "entry": entry, "cap": None, "profiles": [],
                         "local": True, "future": None})
            continue
        cap = min(entry.pages or PARSE_MAX_PAGES_PER_FILE, PARSE_MAX_PAGES_PER_FILE)
        wanted, profiles = _budget_pages(entry, cap, failures)
        jobs.append({"decision": decision, "entry": entry, "cap": cap, "profiles": profiles,
                     "local": False,
                     # The owner is the announcement: the queue rotates between announcements but
                     # keeps this one's own file order, which is already sorted by priority.
                     "future": submit_parse(decision, entry, wanted, cap)})

    for job in jobs:
        decision, entry = job["decision"], job["entry"]
        profiles: list[dict] = job["profiles"]
        cap = job["cap"]
        try:
            if job["local"]:
                pages = read_native(entry.path, entry.fmt)
            else:
                response = job["future"].result()
                pages = as_pages(response)
                decision.parse_meta = (response.get("meta") or {})
        except ParseError as exc:
            code = "parse_unreachable" if str(exc.reason).startswith("parse_unreachable") else "parse_http_failed"
            failures.append(Failure(failure_code=code, failure_message=str(exc.reason)[:200], location=decision.file_id))
            decision.read_strategy = "skip"
            decision.reason = "parse_failed:" + str(exc.reason)[:80]
            emit(decision, {"event": "failed", "reason": exc.reason})
            continue
        except CancelledError:
            failures.append(Failure(failure_code="document_parse_failed", location=decision.file_id,
                                    failure_message="parse_cancelled_before_start"))
            decision.read_strategy = "skip"
            decision.reason = "parse_cancelled_before_start"
            emit(decision, {"event": "cancelled_before_start"})
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


def _validate_candidates(parsed: dict) -> str:
    if not isinstance(parsed.get("candidates"), list):
        return "需要 candidates 数组"
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
                                file_award_notices: dict[str, bool] | None = None,
                                need_resolutions: list | None = None,
                                reception: list | None = None):
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
            "source_segments": page.get("source_segments") or page_segments(decision.file_id, {
                **page, "text": page["text"][:PAGE_CONTEXT_CHARS],
                "tables": page["tables"][:PAGE_CONTEXT_TABLES]}),
        })

    plans_by_no = {project.package_no: project for project in plans.projects}

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

    jobs: list[dict] = []
    for package_no, contexts in by_package.items():
        for chunk_start in range(0, len(contexts), PARSE_RUN_PAGES):
            jobs.append({"package_no": package_no,
                         "chunk": contexts[chunk_start:chunk_start + PARSE_RUN_PAGES],
                         "split": chunk_start + PARSE_RUN_PAGES < len(contexts),
                         "pages": len(contexts)})

    # One independent call per (package, chunk). Candidate ids and the package_amounts order are
    # assigned below, in the original job order, so nothing downstream can tell this ran in parallel.
    for asked in parallel_map(ask, jobs):
        package_no, chunk = asked["package_no"], asked["chunk"]
        payload = asked["payload"]
        if asked["split"]:
            failures.append(Failure(failure_code="page_context_split",
                                    failure_message="包 %s 页数 %d，拆成多次调用"
                                                    % (package_no, asked["pages"]),
                                    location=str(package_no)))
        produced = asked["produced"]
        if produced is None:
            failures.append(asked["failure"])
            continue
        receipt = {"package_scope": package_no,
                   "source_refs": [segment["source_ref"] for context in chunk for segment in context["source_segments"]],
                   **produced.get("reception", {})}
        if reception is not None:
            reception.append(receipt)
        if receipt.get("rejected_items") or receipt.get("unrepaired"):
            failures.append(Failure(failure_code="llm_schema_invalid", location=package_no,
                failure_message="独立输出已保留；接收审计含结构错误或未归属金额"))
        for entry in produced.get("candidates") or []:
            entity = entry.get("entity_type")
            if entity not in {"cob", "sub"}:
                continue
            file_id = entry.get("file_id") or (chunk[0]["file_id"] if chunk else None)
            context = resolve_context(entry, chunk)
            if context is None:
                failures.append(Failure(failure_code="llm_schema_invalid", location=str(file_id),
                                        failure_message="候选未引用唯一来源页或片段"))
                continue
            file_id = context["file_id"]
            file_class = (file_classes or {}).get(file_id or "", DEFAULT_FILE_CLASS)
            item_package = entry.get("package_no") or payload["current_package"]["package_no"]
            project_id = make_project_id(project_name, str(item_package)) if item_package and project_name else None
            segment = next((item for item in context["source_segments"]
                            if item["source_ref"] == entry.get("source_ref")), None)
            candidates.append(seal_candidate(
                candidate_id="cand_%06d" % seq, entity_type=entity, project_id=project_id,
                package_no=None if item_package is None else str(item_package),
                source_type=context.get("source_type", "pdf"), file_id=file_id,
                source_priority=int(CLASS_PRIORITY.get(file_class, DEFAULT_SOURCE_PRIORITY)),
                raw_fields=entry.get("fields") or {}, issues=entry.get("issues") or [],
                source_class=file_class, page_no=context.get("page_no"),
                table_index=segment.get("table_index") if segment else _int_or_none(entry.get("table_index")),
                row_index=segment.get("row_index") if segment else None,
                row_text=segment.get("text") if segment else _row_text_of(entry),
                source_refs=[segment["source_ref"]] if segment else [],
                quote_supplier=entry.get("quote_supplier") or context.get("quote_supplier"),
                bidder_supplier=entry.get("bidder_supplier"),
                winner_supplier=entry.get("winner_supplier")
                or payload["current_package"]["winner_supplier"]))
            seq += 1
        if package_amounts is not None:
            package_amounts.extend(_package_amount_records(
                produced.get("package_amounts"), chunk, package_no, file_classes, file_names))
        if need_resolutions is not None:
            need_resolutions.extend(item for item in produced.get("need_resolutions", []) if isinstance(item, dict))
    return candidates, failures, quality, seq


def extract_screen_evidence(run_id, project_name, plans, decisions, index, llm, seq, counter=None,
                            package_amounts=None, winners=None, reception=None):
    """Feed only model-cited, already seen native text to the regular extraction function."""
    parsed, selected = {}, []
    for decision in decisions.file_decisions:
        if not decision.evidence_refs or decision.read_strategy != "target_pages":
            continue
        for segment in decision.native_segments:
            if segment["source_ref"] not in decision.evidence_refs:
                continue
            page = {"page_no": segment["page_no"], "text": segment["text"], "tables": [],
                "chars": len(segment["text"]), "source": (getattr(decision, "screen", None) or {}).get("format", "pdf"),
                    "source_segments": [segment]}
            parsed.setdefault(decision.file_id, []).append(page)
            selected.append(PageDecision(file_id=decision.file_id, page_no=page["page_no"], relevance=1,
                                         package_scope="announcement", extraction_mode="text",
                                         expected_fields=decision.expected_fields, reason="model_cited_native_evidence"))
    resolutions = []
    candidates, failures, quality, seq = extract_attachment_candidates(
        run_id, project_name, plans, PageDecisions(run_id=run_id, status="success", page_decisions=selected),
        parsed, llm, seq, counter=counter,
        file_classes={item.file_id: item.file_class for item in decisions.file_decisions},
        file_names=file_names_of(index),
        file_found_fields={item.file_id: item.expected_fields for item in decisions.file_decisions},
        file_quote_suppliers={item.file_id: item.quote_supplier for item in decisions.file_decisions if item.quote_supplier},
        winner_suppliers=winners, package_amounts=package_amounts, need_resolutions=resolutions,
        reception=reception)
    return candidates, failures, quality, seq, parsed, resolutions


def apply_native_resolutions(plans, decisions, candidates, resolutions, *, update_files=True):
    """Found evidence is not final resolution: require extracted values and matching ownership."""
    needs = {need.get("need_id"): need for plan in plans.projects for need in plan.needs}
    resolved = set()
    coverage = {}
    for resolution in resolutions:
        need = needs.get(resolution.get("need_id"))
        if need is None or need.get("purpose") != "fill_field" or need["field"] != resolution.get("field"):
            continue
        target = resolution.get("target_candidate_id")
        if target not in need.get("target_candidate_ids", []):
            continue
        sources = [item for item in candidates if item.evidence and
                       resolution.get("source_ref") in item.evidence.source_refs and
                       item.package_no == need["package_no"] and need["field"] in item.fields and
                       item.fields[need["field"]].status == "present"]
        # SUB resolutions must identify a unique observation, not any supplier on the same page.
        if need.get("target_entity") == "sub":
            sources = [item for item in sources if resolution.get("source_candidate_ref") in item.evidence.source_refs
                       and item.entity_type == "sub"
                       and (item.fields[need["field"]].normalized_value if item.fields[need["field"]].normalized_value is not None
                            else item.fields[need["field"]].raw_value) not in (None, "", "unknown")]
        source = sources[0] if len(sources) == 1 else None
        if source is not None:
            coverage.setdefault(need["need_id"], {})[target] = resolution["source_ref"]
    for need_id, targets in coverage.items():
        need = needs[need_id]
        need["source_refs"] = list(dict.fromkeys(targets.values()))
        need["covered_target_candidate_ids"] = list(targets)
        if set(need["target_candidate_ids"]) <= set(targets):
            need["state"] = "evidence_ready"
            resolved.add(need_id)
    if not update_files:
        return resolved
    for decision in decisions.file_decisions:
        if not decision.evidence_refs:
            continue
        packages = {item.package_no for item in candidates if item.evidence and
                    set(decision.evidence_refs) & set(item.evidence.source_refs) and item.package_no is not None}
        hit_ids = getattr(decision, "needs_hit_ids", [])
        relevant = [need for need in needs.values() if need["field"] in decision.expected_fields
                    and need.get("purpose") != "discover_objects" and need.get("package_no") in packages
                    and (not hit_ids or need.get("need_id") in hit_ids)]
        # A discovery or binding task cannot be declared done by an ordinary field observation.
        complete = bool(relevant) and all(need.get("need_id") in resolved for need in relevant)
        if complete and not (getattr(decision, "screen", None) or {}).get("requires_deep_parse", True):
            decision.read_strategy = "skip"
            decision.reuse_status = "native_evidence_ready"
            # Discovery stays pending. This file was judged by the model not to need deeper parsing;
            # that does not certify the announcement's complete object inventory.
            decision.reason += ":native_evidence_ready"
        else:
            decision.reuse_status = "native_evidence_partial"
    return resolved


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


def carry_native_resolution_state(before, after):
    """Carry only the same target references, without changing evidence/identity criteria."""
    previous = {need["need_id"]: need for plan in before.projects for need in plan.needs}
    for plan in after.projects:
        for need in plan.needs:
            old = previous.get(need["need_id"])
            if not old or old.get("package_no") != need.get("package_no") or old.get("purpose") != need.get("purpose"):
                continue
            covered = set(old.get("covered_target_candidate_ids", [])) & set(need.get("target_candidate_ids", []))
            if not covered:
                continue
            need["covered_target_candidate_ids"] = sorted(covered)
            need["source_refs"] = list(old.get("source_refs", []))
            if set(need["target_candidate_ids"]) <= covered:
                need["state"] = "evidence_ready"


def _known_winner(package_no: str, chunk: list[dict], winner_suppliers: dict[str, list[str]] | None,
                  file_quote_suppliers: dict[str, str] | None,
                  file_award_notices: dict[str, bool] | None) -> str | None:
    """The winner step 6 should copy rather than look for.

    Two upstream sources, in order: the award text of the announcement, then a document the screen
    gate read as an award notice - such a document names the winner by definition, so its supplier
    is already known and re-deriving it from the page would only risk a different spelling.
    """
    known = (winner_suppliers or {}).get(package_no) or []
    if len(known) == 1:
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
        package_no = str(item.get("package_no") or "").strip()
        raw_text = str(item.get("raw_text") or "").strip()
        if not package_no or not raw_text:
            continue
        context = resolve_context(item, chunk)
        if context is None:
            continue
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
            file_class=(file_classes or {}).get(file_id or "", DEFAULT_FILE_CLASS),
            amount_kind=item.get("amount_kind", "unknown"), source_ref=item.get("source_ref")))
    return out
