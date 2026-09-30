"""Run one announcement through the nine main-route steps."""

from __future__ import annotations

import time
from collections import Counter
from pathlib import Path

from ict.catalog import Catalog
from ict.config import ATTACHMENTS_ROOT, DATA_HTML
from ict.html_context import parse_notice, winner_hints
from ict.index.build import load_index
from ict.llm import LLMClient, build_client
from ict.schemas import CandidateFile, Failure, RunReport
from ict.state import RunStore, write_json
from ict.steps.s01_understand import understand
from ict.steps.s02_html import extract_html_candidates, understand_tables
from ict.steps.s03_plan import plan_projects
from ict.steps.s04_attach import extract_attachment_candidates, locate_pages, triage_files
from ict.steps.s07_normalize import normalize_candidates
from ict.steps.s08_merge import merge_projects
from ict.steps.s08b_repair import repair_packages


def _status_from_failures(base: str, failures: list[Failure]) -> str:
    if base == "failed":
        return "failed"
    if failures or base == "partial":
        return "partial"
    return base


def run_announcement(announcement_id: str, llm: LLMClient | None = None, html_dir: Path = DATA_HTML) -> RunReport:
    started = time.perf_counter()
    html_path = html_dir / f"{announcement_id}.html"
    notice = parse_notice(html_path)
    client = build_client() if llm is None else llm
    store = RunStore(announcement_id)
    catalog = Catalog()
    calls: Counter[str] = Counter()
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

    index = load_index(announcement_id, ATTACHMENTS_ROOT)
    has_attachment = index is not None and index.attachment_directory is not None and bool(index.files)
    store.begin("plan_project_gaps")
    plans = plan_projects(store.run_id, understanding, html_candidates, has_attachment)
    store.write_output("plan_project_gaps", plans)
    store.finish("plan_project_gaps", plans.status)
    store.state.counts["projects"] = len(plans.projects)
    all_failures.extend(plans.failures)

    attachment_candidates = []
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

        store.begin("locate_pages")
        page_decisions = locate_pages(store.run_id, plans, file_decisions, index, client, calls)
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
            index,
            client,
            seq,
            counter=calls,
            file_classes={item.file_id: item.file_class for item in file_decisions.file_decisions},
        )
        attach_file = CandidateFile(
            run_id=store.run_id,
            status="skipped" if not page_decisions.page_decisions else ("partial" if attach_failures else "success"),
            candidates=attachment_candidates,
            failures=attach_failures,
            page_quality=quality,
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
    merged = merge_projects(store.run_id, understanding, normalized.candidates)
    store.write_output("merge_candidates", merged)
    store.finish("merge_candidates", merged.status)

    store.begin("repair_packages")
    check_failures = repair_packages(merged, winner_hints(notice, package_nos), client, calls)
    all_failures.extend(check_failures)
    merged.failures.extend(check_failures)
    store.write_output("repair_packages", merged)
    store.finish("repair_packages", "partial" if check_failures else "success")
    store.state.counts["final_cobs"] = sum(len(project.cobs) for project in merged.projects)
    store.state.counts["final_subs"] = sum(len(project.subs) for project in merged.projects)
    public = [project.model_dump(exclude={"provenance"}) for project in merged.projects]
    write_json(store.directory / "projects.json", public)

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


def _first_message(failures: list[Failure]) -> str | None:
    return failures[0].failure_message if failures else None


def _finish(store: RunStore, calls: Counter, failures: list[Failure], started: float) -> RunReport:
    summary = Counter(item.failure_code for item in failures)
    report = RunReport(
        run_id=store.run_id,
        announcement_id=store.announcement_id,
        status=store.state.status,
        duration_ms=int((time.perf_counter() - started) * 1000),
        counts=store.state.counts,
        llm_calls=dict(calls),
        failure_summary=[{"failure_code": code, "count": count} for code, count in sorted(summary.items())],
        review_required=store.state.review_required or store.state.status != "success",
    )
    store.begin("persist_and_report")
    store.write_output("persist_and_report", report)
    store.finish("persist_and_report", "success")
    store.state.current_step = "persist_and_report"
    store.save()
    return report
