"""Run one announcement through the nine main-route steps."""

from __future__ import annotations

import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ict.catalog import Catalog
from ict.concurrency import CallCounter, counts_snapshot
from ict.config import ATTACHMENTS_ROOT, DATA_HTML, concurrency_settings
from ict.html_context import parse_notice, winner_hints
from ict.index.build import build_index, load_index
from ict.llm import LLMClient, build_client
from ict.schemas import CandidateFile, Failure, MergeEvidence, RunReport
from ict.state import RunStore, write_json
from ict.steps.s01_understand import understand
from ict.steps.s02_html import extract_html_candidates, understand_tables
from ict.steps.s03_plan import plan_projects
from ict.steps.s04_attach import (
    extract_attachment_candidates,
    file_names_of,
    locate_pages,
    parse_pages,
    triage_files,
)
from ict.steps.s07_normalize import normalize_candidates
from ict.steps.s08_merge import merge_projects
from ict.steps.s08b_repair import repair_packages


def _status_from_failures(base: str, failures: list[Failure]) -> str:
    if base == "failed":
        return "failed"
    if failures or base == "partial":
        return "partial"
    return base


def run_announcement(announcement_id: str, llm: LLMClient | None = None, html_dir: Path = DATA_HTML,
                    attachments_root: Path | None = None, persist: bool = True) -> RunReport:
    started = time.perf_counter()
    html_path = html_dir / f"{announcement_id}.html"
    notice = parse_notice(html_path)
    client = build_client() if llm is None else llm
    store = RunStore(announcement_id, persist=persist)
    catalog = Catalog()
    calls = CallCounter()
    all_failures: list[Failure] = []

    store.begin("understand_announcement")
    understanding = understand(store.run_id, notice, client, calls)
    store.write_output("understand_announcement", understanding)
    store.finish("understand_announcement", understanding.status, _first_code(understanding.failures), _first_message(understanding.failures))
    all_failures.extend(understanding.failures)
    if understanding.status == "failed" or understanding.package_mode == "unclear" or not understanding.project_name:
        store.state.status = "failed"
        return _finish(store, calls, all_failures, started)

    package_nos = [package.package_no for package in understanding.packages]
    # Needed by step 6 (so it copies the winner instead of re-deriving it) and by step 8b.
    award_hints = winner_hints(notice, package_nos)
    store.begin("understand_html_tables")
    tables = understand_tables(store.run_id, notice, package_nos, client, calls)
    store.write_output("understand_html_tables", tables)
    store.finish("understand_html_tables", tables.status)
    all_failures.extend(tables.failures)

    store.begin("extract_html_candidates")
    html_candidates, html_failures, seq = extract_html_candidates(
        store.run_id, notice, tables, understanding.project_name or "", client, counter=calls, known_packages=package_nos
    )
    html_file = CandidateFile(
        run_id=store.run_id,
        status="failed" if not html_candidates else ("partial" if html_failures else "success"),
        candidates=html_candidates,
        failures=html_failures,
    )
    store.write_output("extract_html_candidates", html_file)
    store.finish("extract_html_candidates", html_file.status)
    store.state.counts["html_candidates"] = len(html_candidates)
    all_failures.extend(html_failures)

    attachments_dir = (attachments_root or ATTACHMENTS_ROOT) / announcement_id
    index = load_index(announcement_id, attachments_root or ATTACHMENTS_ROOT)
    if index is None and attachments_dir.is_dir():
        index = build_index(announcement_id, attachments_root or ATTACHMENTS_ROOT)
    has_attachment = index is not None and index.attachment_directory is not None and bool(index.files)
    store.begin("plan_project_gaps")
    plans = plan_projects(store.run_id, understanding, html_candidates, has_attachment)
    store.write_output("plan_project_gaps", plans)
    store.finish("plan_project_gaps", plans.status)
    store.state.counts["projects"] = len(plans.projects)
    all_failures.extend(plans.failures)

    attachment_candidates: list = []
    package_amounts: list = []
    file_decisions = None
    page_decisions = None
    parsed: dict[str, list[dict]] = {}
    if index is None:
        store.begin("triage_files")
        skipped = triage_files(store.run_id, plans, None, client)
        store.write_output("triage_files", skipped)
        store.finish("triage_files", "skipped", "attachment_index_miss", "附件索引不存在")
        for step in ("locate_pages", "extract_attachment_candidates"):
            store.begin(step)
            store.finish(step, "skipped")
        all_failures.extend(skipped.failures)
    else:
        store.state.counts["files"] = len(index.files)
        store.begin("triage_files")
        file_decisions = triage_files(store.run_id, plans, index, client, calls)
        store.write_output("triage_files", file_decisions)
        store.finish("triage_files", file_decisions.status)
        store.state.counts["selected_files"] = sum(1 for item in file_decisions.file_decisions if item.read_strategy == "target_pages")
        all_failures.extend(file_decisions.failures)

        parse_failures: list[Failure] = []
        store.begin("parse_pages")
        parsed, parse_summary = parse_pages(store.run_id, file_decisions, index, parse_failures)
        parsed_status = "skipped" if not parsed else ("partial" if parse_failures else "success")
        store.write_output("parse_pages", {"run_id": store.run_id, "status": parsed_status, "files": parse_summary,
                                           "failures": [failure.model_dump() for failure in parse_failures]})
        store.finish("parse_pages", parsed_status)
        store.state.counts["parsed_pages"] = sum(len(pages) for pages in parsed.values())
        store.write_output("triage_files", file_decisions)
        all_failures.extend(parse_failures)

        store.begin("locate_pages")
        page_decisions = locate_pages(store.run_id, plans, file_decisions, parsed, index, client, calls)
        store.write_output("locate_pages", page_decisions)
        store.finish("locate_pages", page_decisions.status)
        store.state.counts["selected_pages"] = len(page_decisions.page_decisions)
        all_failures.extend(page_decisions.failures)

        store.begin("extract_attachment_candidates")
        attachment_candidates, attach_failures, quality, seq = extract_attachment_candidates(
            store.run_id,
            understanding.project_name or "",
            plans,
            page_decisions,
            parsed,
            client,
            seq,
            counter=calls,
            file_classes={item.file_id: item.file_class for item in file_decisions.file_decisions},
            file_names=file_names_of(index),
            file_found_fields={item.file_id: item.expected_fields for item in file_decisions.file_decisions},
            package_amounts=package_amounts,
            file_quote_suppliers={item.file_id: item.quote_supplier
                                  for item in file_decisions.file_decisions if item.quote_supplier},
            winner_suppliers=award_hints,
            file_award_notices={item.file_id: True for item in file_decisions.file_decisions
                                if item.is_award_notice},
        )
        attach_file = CandidateFile(
            run_id=store.run_id,
            status="skipped" if not page_decisions.page_decisions else ("partial" if attach_failures else "success"),
            candidates=attachment_candidates,
            failures=attach_failures,
            page_quality=quality,
            package_amounts=package_amounts,
        )
        store.write_output("extract_attachment_candidates", attach_file)
        store.finish("extract_attachment_candidates", attach_file.status)
        store.state.counts["attachment_candidates"] = len(attachment_candidates)
        all_failures.extend(attach_failures)

    combined = html_candidates + attachment_candidates
    store.begin("normalize_candidates")
    normalized = normalize_candidates(store.run_id, combined, catalog)
    store.write_output("normalize_candidates", normalized)
    store.finish("normalize_candidates", normalized.status)
    store.state.counts["normalized_candidates"] = len(normalized.candidates)
    all_failures.extend(normalized.failures)

    store.begin("merge_candidates")
    evidence = _merge_evidence(notice, tables, understanding, index, file_decisions, page_decisions,
                               parsed, package_amounts, award_hints)
    store.write_output("merge_evidence", evidence)
    merged = merge_projects(store.run_id, understanding, normalized.candidates, package_amounts,
                            evidence=evidence, llm=client, counter=calls)
    store.write_output("merge_candidates", merged)
    store.finish("merge_candidates", merged.status)

    store.begin("repair_packages")
    check_failures = repair_packages(merged, award_hints, client, calls)
    all_failures.extend(check_failures)
    merged.failures.extend(check_failures)
    store.write_output("repair_packages", merged)
    store.finish("repair_packages", "partial" if check_failures else "success")
    store.state.counts["final_cobs"] = sum(len(project.cobs) for project in merged.projects)
    store.state.counts["final_subs"] = sum(len(project.subs) for project in merged.projects)
    public = [project.model_dump(exclude={"provenance"}) for project in merged.projects]
    if persist:
        write_json(store.directory / "projects.json", public)
    store.projects = public

    final = "success"
    if understanding.status == "failed" or not merged.projects:
        final = "failed"
    elif all_failures or merged.status == "partial":
        final = "partial"
    store.state.status = final
    store.state.review_required = final != "success"
    return _finish(store, calls, all_failures, started)


def _first_code(failures: list[Failure]) -> str | None:
    return failures[0].failure_code if failures else None


def run_batch(announcement_ids: list[str], workers: int | None = None,
              **kwargs) -> dict[str, RunReport]:
    """Run several announcements at once, one thread each.

    Announcements share nothing but the gates in `ict.concurrency`: each keeps its own run
    directory, its own parsed pages and its own candidates. A repeated id runs once, because two
    threads writing into the same run directory could only corrupt it.

    `workers=1` is the plain sequential loop, which is what a deployment that wants no
    announcement-level concurrency should pass.
    """
    ids = list(dict.fromkeys(announcement_ids))
    if not ids:
        return {}
    default = int(concurrency_settings()["announcement_workers"])
    limit = max(1, int(workers) if workers is not None else default)
    if limit == 1 or len(ids) == 1:
        return {announcement_id: run_announcement(announcement_id, **kwargs)
                for announcement_id in ids}
    with ThreadPoolExecutor(max_workers=min(limit, len(ids)), thread_name_prefix="ict-run") as pool:
        futures = {announcement_id: pool.submit(run_announcement, announcement_id, **kwargs)
                   for announcement_id in ids}
        return {announcement_id: future.result() for announcement_id, future in futures.items()}


def _merge_evidence(notice, tables, understanding, index, file_decisions, page_decisions,
                    parsed: dict[str, list[dict]], package_amounts: list,
                    award_hints: dict[str, list[str]]) -> MergeEvidence:
    """Assemble what step 8 needs but cannot recover from candidates alone.

    Every value here is already in scope inside `run_announcement`; this only carries it forward.
    Nothing is judged: file names, table roles, page text and amount observations are handed over
    as they are, and step 8 decides what they mean.
    """
    html_tables: dict[str, dict] = {}
    by_index = {table.table_index: table for table in notice.tables}
    for understood in (tables.tables if tables is not None else []):
        table = by_index.get(understood.table_index)
        html_tables[str(understood.table_index)] = {
            "table_role": understood.table_role,
            "row_grain": understood.row_grain,
            "package_scope": understood.package_scope,
            "section": (table.section if table is not None else None) or "",
            "headers": (table.headers if table is not None else []) or [],
            "issues": list(understood.issues or []),
        }
    names = file_names_of(index)
    attachment_pages: dict[str, dict] = {}
    selected = {(item.file_id, item.page_no) for item in (page_decisions.page_decisions if page_decisions else [])}
    for file_id, pages in (parsed or {}).items():
        for page in pages:
            if selected and (file_id, page["page_no"]) not in selected:
                continue
            attachment_pages["%s:%s" % (file_id, page["page_no"])] = {
                "file_name": names.get(file_id, file_id),
                "text_head": (page.get("text") or "")[:400],
                "table_headers": [table.get("headers") for table in (page.get("tables") or [])],
            }
    observations: dict[str, list[dict]] = {}

    def _observe(package_no: str, raw, yuan, origin: str, location: dict | None = None) -> None:
        if raw in (None, "") and yuan is None:
            return
        observations.setdefault(str(package_no), []).append(
            {"raw_text": raw, "amount_yuan": yuan, "origin": origin, **(location or {})})

    single = understanding.package_mode == "single"
    for package in understanding.packages:
        amount = package.package_amount
        if amount is not None:
            _observe(package.package_no, amount.raw_text, amount.amount_yuan, "announcement_package")
        for extra in package.amount_alternatives or []:
            _observe(package.package_no, extra.raw_text, extra.amount_yuan, "announcement_alternative")
        if single and understanding.summary_amount is not None:
            _observe(package.package_no, understanding.summary_amount.raw_text,
                     understanding.summary_amount.amount_yuan, "announcement_summary")
    for item in package_amounts or []:
        _observe(item.package_no, item.raw_text, item.amount_yuan, "attachment",
                 {"file_id": item.file_id, "file_name": item.file_name, "page_no": item.page_no,
                  "file_class": item.file_class})
    return MergeEvidence(
        file_names=names,
        file_classes={item.file_id: item.file_class for item in (file_decisions.file_decisions if file_decisions else [])},
        file_quote_suppliers={item.file_id: item.quote_supplier
                              for item in (file_decisions.file_decisions if file_decisions else [])
                              if item.quote_supplier},
        file_award_notices={item.file_id: True
                            for item in (file_decisions.file_decisions if file_decisions else [])
                            if item.is_award_notice},
        html_tables=html_tables,
        attachment_pages=attachment_pages,
        winner_hints={key: list(value) for key, value in (award_hints or {}).items()},
        amount_observations=observations,
    )


def _first_message(failures: list[Failure]) -> str | None:
    return failures[0].failure_message if failures else None


def _finish(store: RunStore, calls: CallCounter, failures: list[Failure], started: float) -> RunReport:
    summary = Counter(item.failure_code for item in failures)
    report = RunReport(
        run_id=store.run_id,
        announcement_id=store.announcement_id,
        status=store.state.status,
        duration_ms=int((time.perf_counter() - started) * 1000),
        counts=store.state.counts,
        llm_calls=counts_snapshot(calls),
        failure_summary=[{"failure_code": code, "count": count} for code, count in sorted(summary.items())],
        review_required=store.state.review_required or store.state.status != "success",
    )
    report.projects = getattr(store, "projects", None)
    store.begin("persist_and_report")
    store.write_output("persist_and_report", report)
    store.finish("persist_and_report", "success")
    store.state.current_step = "persist_and_report"
    store.save()
    return report
