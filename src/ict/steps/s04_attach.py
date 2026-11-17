"""Attachments: native extraction or page inspection, selected OCR, then extraction."""
from __future__ import annotations
import json
import time
from collections import defaultdict
from pathlib import Path
from concurrent.futures import CancelledError
from ict.config import (ATTACHMENTS_ROOT, MAX_PAGES_PER_FILE, MAX_PAGES_PER_ANNOUNCEMENT,
                        PARSE_MAX_PAGES_PER_FILE, FILE_CLASSES, parse_settings)
from ict.concurrency import PARSE_QUEUE, parallel_map
from ict.packages import hint_reference, reference_matches, package_context
from ict.parse.client import ParseClient, ParseError, as_pages
from ict.parse.census import ARCHIVE_FORMATS, sniff
from ict.parse.peek import page_profiles
from ict.schemas import AttachmentIndex, IndexedFile, Failure, CandidateFile
from ict.llm import LLMError, complete_json
from ict.observations import seal_observations, seal_amounts
from ict.receiving import ExtractionReceiver
from ict.parse.screen import inspect_page, NAME_IGNORE
import re

FILE_PACKAGE_RE = re.compile(r"(?:标\s*包|采购包|合同包|第)?\s*([0-9]+|[A-Za-z])\s*包|(?:标\s*包|采购包|合同包)\s*[:：]?\s*([0-9]+|[A-Za-z])")
PAGE_PACKAGE_RE = re.compile(r"第\s*([0-9]+)\s*包|(?:标\s*包|采购包|合同包|包号|包)\s*[:：]?\s*([0-9]+|[A-Za-z])(?![0-9A-Za-z])")

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


def process_attachment_pages(run_id, project_name, plans, index, llm, seq_start, counter=None, *, progress=None, owner=None,
                             known_observations=(), announcement=None):
    """A fixed need snapshot, one page decision, selected OCR, and observed candidates."""
    from contextlib import ExitStack
    from ict.attachment_pages import source_pages, split_page
    failures, records, candidates, amounts, contexts, reception = [], [], [], [], [], []
    needs = [need for plan in plans.projects for need in plan.needs]
    registry = [package_context(plan) for plan in plans.projects]
    def amount_input(amount):
        return (amount.model_dump(include={'raw_text', 'amount_kind', 'source_ref'}) |
                {'unit_header': amount.binding.get('unit_header')}) if amount else None
    inspection_registry = ([package_context(package) |
        {'package_amount': amount_input(package.package_amount),
         'amount_alternatives': [amount_input(amount) for amount in package.amount_alternatives]}
        for package in announcement.packages] if announcement else registry)
    summary_amount = amount_input(announcement.summary_amount) if announcement else None
    seq = seq_start
    if index is None:
        return CandidateFile(run_id=run_id, status="skipped"), contexts, seq

    def emit(file_id, event, **details):
        if progress:
            progress({"file_id": file_id, "time": time.time(), "event": event, **details})

    def fail(exc, record, code="document_parse_failed"):
        record["status"], record["error"] = "failed", str(exc)
        failures.append(Failure(failure_code=code, location=record.get("source_ref", record.get("file_id")),
                                failure_message=str(exc)))

    def inspect(job):
        if not job["page"].get("images"):
            return job, {"action": "extract"}, {}
        preview = {"project_name": project_name, "file_id": job["entry"].file_id, "file_name": job["entry"].name,
                   "page_no": job["page"]["page_no"], "package_registry": inspection_registry,
                   "summary_amount": summary_amount,
                   "known_observations": known_observations,
                   "source_segments": [segment for chunk in job["chunks"] for segment in chunk["source_segments"]],
                   "images": job["page"]["images"]}
        try:
            if llm is None:
                raise LLMError("未配置模型")
            answer, audit = inspect_page(llm, preview, needs, counter)
            return job, answer, audit
        except (LLMError, ValueError, NotImplementedError) as exc:
            return job, None, {"error": str(exc), "failure_code": "llm_schema_invalid" if isinstance(exc, ValueError) else "llm_call_failed",
                               **getattr(exc, "page_audit", {})}

    with ExitStack() as stack:
        jobs = []
        for item in index.files:
            entry = _entry(index, item)
            file_record = {"file_id": entry.file_id, "file_name": entry.name}
            if (match := NAME_IGNORE.search(entry.name)):
                records.append({**file_record, "status": "skipped", "method": "rule_name_ignore",
                                "reason": "rule_name_ignore:" + match.group(0)})
                continue
            if entry.fmt in ARCHIVE_FORMATS:
                records.append(file_record)
                fail(ValueError("archive_not_expanded:" + entry.fmt), file_record)
                continue
            # Trust sniffed bytes even if an older index supplied a misleading suffix.
            try:
                entry.fmt, entry.note = sniff(entry.path)
                def choose(proxy):
                    cap = min(proxy.pages or PARSE_MAX_PAGES_PER_FILE, PARSE_MAX_PAGES_PER_FILE)
                    wanted, profiles = _budget_pages(proxy, cap, failures)
                    selected = wanted or list(range(1, cap + 1))
                    if (proxy.pages or 0) > len(selected):
                        records.append({**file_record, "status": "uncovered", "reason": "parse_page_budget",
                                        "pages_total": proxy.pages, "covered_pages": selected,
                                        "uncovered_count": proxy.pages - len(selected)})
                    return selected
                for page in source_pages(entry, choose, stack):
                    chunks = split_page(entry.file_id, entry.name, page)
                    jobs.append({"entry": entry, "page": page, "chunks": chunks,
                                 "record": {**file_record, "page_no": page["page_no"],
                                            "coordinate_kind": page.get("coordinate_kind")}})
            except (OSError, ValueError, RuntimeError) as exc:
                records.append(file_record)
                fail(exc, file_record)

        selected, per_file, total = [], defaultdict(int), 0
        for job, answer, audit in parallel_map(inspect, jobs):
            record = job["record"]
            records.append(record)
            record["inspection"] = audit
            if answer is None:
                fail(ValueError(audit["error"]), record, audit["failure_code"])
                continue
            record.update(answer)
            if answer["action"] == "skip":
                record["status"] = "skipped"
                continue
            file_id = job["entry"].file_id
            if per_file[file_id] >= MAX_PAGES_PER_FILE or total >= MAX_PAGES_PER_ANNOUNCEMENT:
                record.update(status="uncovered", reason="extraction_page_budget")
                continue
            selected.append(job)
            per_file[file_id] += 1
            total += 1

        # Submit only model-selected physical pages. No file fallback and no second page gate.
        ocr_jobs = {}
        for job in selected:
            if job["record"]["action"] == "ocr":
                ocr_jobs.setdefault(job["entry"].file_id, []).append(job)
        client, pending = None, []
        for file_id, group in ocr_jobs.items():
            client = client or _client()
            path = group[0]["page"].get("parse_path", group[0]["entry"].path)
            wanted = [job["page"]["page_no"] for job in group]
            emit(file_id, "queued", pages=wanted)
            def invoke(*, cancel, path=path, wanted=wanted, file_id=file_id):
                emit(file_id, "dequeued")
                return client.parse(path, pages=wanted, max_pages=PARSE_MAX_PAGES_PER_FILE,
                                    cancel=cancel, progress=lambda event: progress({"file_id": file_id, "time": time.time(), **event}) if progress else None)
            pending.append((group, PARSE_QUEUE.submit_cancellable(owner or run_id, invoke)))
        for group, future in pending:
            try:
                response = future.result()
                parsed = {page["page_no"]: page for page in as_pages(response)}
                for job in group:
                    number = job["page"]["page_no"]
                    if number not in parsed:
                        fail(ValueError("OCR未返回所选页:%s" % number), job["record"])
                        job["chunks"] = []
                        continue
                    page = parsed[number] | {"source_type": "pdf", "coordinate_kind": job["page"].get("coordinate_kind")}
                    job["chunks"] = split_page(job["entry"].file_id, job["entry"].name, page)
                    job["record"]["parse_meta"] = response.get("meta", {})
            except (ParseError, CancelledError, ValueError) as exc:
                for job in group:
                    fail(exc, job["record"])
                    job["chunks"] = []

        extraction_jobs, per_file, total = [], defaultdict(int), 0
        for job in selected:
            for chunk in job["chunks"]:
                file_id = job["entry"].file_id
                if per_file[file_id] >= MAX_PAGES_PER_FILE or total >= MAX_PAGES_PER_ANNOUNCEMENT:
                    records.append({"file_id": file_id, "source_ref": chunk["source_segments"][0]["source_ref"],
                                    "status": "uncovered", "reason": "extraction_context_budget"})
                    continue
                chunk.pop("images", None)
                chunk["kind"] = job["record"].get("kind", "unknown")
                extraction_jobs.append((chunk, job["record"]))
                per_file[file_id] += 1
                total += 1

        def extract(job):
            chunk, record = job
            notes = {}
            payload = {"project_name": project_name, "package_registry": registry, "needs": needs,
                       "page_contexts": [chunk]}
            try:
                if llm is None:
                    raise LLMError("未配置模型")
                answer = complete_json(llm, step="extract_attachment_candidates", prompt_version="attachment-extract-v10",
                    user=json.dumps(payload, ensure_ascii=False), validate=_validate_extraction,
                    counter=counter, receiver=ExtractionReceiver([chunk]), notes=notes)
                return chunk, record, answer, notes, None
            except (LLMError, ValueError) as exc:
                return chunk, record, None, notes, exc

        for chunk, record, answer, notes, error in parallel_map(extract, extraction_jobs):
            contexts.append(chunk)
            record.setdefault("extraction", []).append(notes)
            if error:
                fail(error, record, "llm_call_failed" if isinstance(error, LLMError) else "llm_schema_invalid")
                continue
            receipt = answer["reception"]
            chunk["kind"] = answer["kind"]
            record.update({key: answer[key] for key in ("kind", "confidence", "reason")})
            reception.append(receipt)
            if receipt["unrepaired"]:
                fail(ValueError("抽取接收存在未修复错误"), record, "llm_schema_invalid")
            elif record.get("status") != "failed":
                record["status"] = "extracted"
            rows, seq = seal_observations(answer["candidates"], [chunk], project_name, seq)
            candidates.extend(rows)
            amounts.extend(seal_amounts(answer["package_amounts"], [chunk]))

    incomplete = failures or any(record.get("status") == "uncovered" for record in records)
    return CandidateFile(run_id=run_id, status="partial" if incomplete else "success",
                         candidates=candidates, package_amounts=amounts, failures=failures,
                         page_quality=records, reception=reception), contexts, seq


def _validate_extraction(value):
    from ict.fields import FRONT_ENUMS
    if not isinstance(value.get("candidates"), list) or not isinstance(value.get("package_amounts"), list):
        return "需要 candidates 和 package_amounts 数组"
    if not isinstance(value.get("reason"), str):
        return "reason需要字符串，记录当前片段判断"
    if value.get("kind") not in FRONT_ENUMS["kind"]:
        from ict.llm import ValidationIssue
        return ValidationIssue(f"原值={value.get('kind')!r}；允许值={FRONT_ENUMS['kind']}", path="kind")
    confidence = value.get("confidence")
    if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        return "confidence 必须为0到1的数字，仅供审计"
    return ""
