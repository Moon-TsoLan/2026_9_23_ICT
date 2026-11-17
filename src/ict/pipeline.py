"""Run one announcement through the nine main-route steps."""

from __future__ import annotations

import time
import json
import threading
import asyncio
from collections import Counter
from pathlib import Path
from uuid import uuid4

from ict.catalog import Catalog
from ict.packages import package_context
from ict.concurrency import CallCounter, counts_snapshot, PARSE_QUEUE
from ict.config import ATTACHMENTS_ROOT, DATA_HTML, concurrency_settings
from ict.html_context import parse_notice
from ict.bindings import bind_packages, winner_names
from ict.evidence import page_segments, html_rows
from ict.index.build import build_index, load_index
from ict.llm import LLMClient, build_client
from ict.schemas import CandidateFile, Failure, MergeEvidence, RunReport
from ict.state import RunStore, write_json, now_iso
from ict.scheduling import MergeScheduler, run_sync
from ict.steps.s01_understand import read_announcement
from ict.steps.s03_plan import plan_projects
from ict.steps.s04_attach import process_attachment_pages
from ict.supplier_policy import finalize_suppliers
from ict.steps.s07_normalize import normalize_candidates
from ict.steps.s08_merge import merge_projects
from ict.steps.s08b_repair import repair_packages
from ict.steps.s09_supplier import complete_suppliers


def _status_from_failures(base: str, failures: list[Failure]) -> str:
    if base == "failed":
        return "failed"
    if failures or base == "partial":
        return "partial"
    return base


def run_announcement(announcement_id: str, llm: LLMClient | None = None, html_dir: Path = DATA_HTML,
                    attachments_root: Path | None = None, persist: bool = True,
                    runs_root: Path | None = None) -> RunReport:
    return run_sync(announcement_chain(announcement_id, llm=llm, html_dir=html_dir,
        attachments_root=attachments_root, persist=persist, runs_root=runs_root))


def announcement_chain(announcement_id: str, llm: LLMClient | None = None, html_dir: Path = DATA_HTML,
                       attachments_root: Path | None = None, persist: bool = True,
                       runs_root: Path | None = None):
    """One chain and one merge suspension point, shared by sync, batch and worker entry points."""
    started = time.perf_counter()
    html_path = html_dir / f"{announcement_id}.html"
    notice = parse_notice(html_path)
    client = build_client() if llm is None else llm
    store = RunStore(announcement_id, persist=persist, runs_root=runs_root)
    # The same announcement id may run concurrently for different accounts. Queue cancellation
    # owns one invocation, never the business id shared by those accounts.
    store.state.parse_owner = f"{store.run_id}:{uuid4().hex}"
    PARSE_QUEUE.begin_owner(store.state.parse_owner)
    catalog = Catalog()
    calls = CallCounter()
    all_failures: list[Failure] = []

    store.begin("understand_announcement")
    understanding, tables, html_file = read_announcement(store.run_id, notice, client, calls)
    store.write_output("understand_announcement", understanding)
    store.finish("understand_announcement", understanding.status, _first_code(understanding.failures), _first_message(understanding.failures))
    all_failures.extend(understanding.failures)
    if understanding.status == "failed" or understanding.package_mode == "unclear" or not understanding.project_name:
        store.state.status = "failed"
        return _finish(store, calls, all_failures, started)

    html_candidates = html_file.candidates
    seq = len(html_candidates) + 1
    store.state.counts['html_candidates'] = len(html_candidates)
    award_hints = winner_names(html_candidates)

    attachments_dir = (attachments_root or ATTACHMENTS_ROOT) / announcement_id
    index = load_index(announcement_id, attachments_root or ATTACHMENTS_ROOT)
    if index is None and attachments_dir.is_dir():
        index = build_index(announcement_id, attachments_root or ATTACHMENTS_ROOT)
    has_attachment = index is not None and index.attachment_directory is not None and bool(index.files)
    if index is not None and index.extraction:
        if persist:
            write_json(store.directory / "00_attachment_preparation.json", index.extraction)
        if index.extraction.get("status") == "partial" or index.extraction.get("failures"):
            all_failures.append(Failure(failure_code="document_parse_failed", location="attachment_preparation",
                                        failure_message="附件未完整解压；详见00_attachment_preparation.json"))
    store.begin("plan_project_gaps")
    plans = plan_projects(store.run_id, understanding, html_candidates, has_attachment)
    store.write_output("plan_project_gaps", plans)
    store.finish("plan_project_gaps", plans.status)
    store.state.counts["projects"] = len(plans.projects)
    all_failures.extend(plans.failures)

    parse_latest, parse_saved = {}, {}
    parse_log_lock = threading.Lock()
    def parse_progress(event):
        with parse_log_lock:
            file_id = event['file_id']
            parse_latest.setdefault(file_id, {}).update(event)
            tick = time.monotonic()
            if event['event'] == 'upload_read' and tick - parse_saved.get(file_id, 0) < 1:
                return
            parse_saved[file_id] = tick
            store.state.parse_progress = dict(parse_latest)
            store.state.updated_at = now_iso()
            store.save()
            if store.persist:
                with (store.directory / '05d_parse_events.jsonl').open('a', encoding='utf-8') as handle:
                    handle.write(json.dumps(parse_latest[file_id], ensure_ascii=False) + '\n')

    store.begin('process_attachment_pages')
    attachment_file, attachment_contexts, seq = process_attachment_pages(
        store.run_id, understanding.project_name or '', plans, index, client, seq,
        calls, progress=parse_progress, owner=store.state.parse_owner, announcement=understanding,
        known_observations=[{'candidate_id': row.candidate_id, 'entity_type': row.entity_type,
            'package_no': row.package_no, 'fields': {key: {'raw_value': obs.raw_value, 'status': obs.status}
                for key, obs in row.fields.items() if obs.raw_value is not None or obs.status != 'missing'},
            'source_refs': row.evidence.source_refs if row.evidence else []} for row in html_candidates])
    store.write_output('process_attachment_pages', attachment_file)
    store.finish('process_attachment_pages', attachment_file.status)
    attachment_candidates = attachment_file.candidates
    package_amounts = attachment_file.package_amounts
    all_failures.extend(attachment_file.failures)
    store.state.counts['files'] = len(index.files) if index else 0
    store.state.counts['attachment_candidates'] = len(attachment_candidates)
    store.state.counts['selected_pages'] = len(attachment_contexts)
    store.state.counts['selected_files'] = len({context['file_id'] for context in attachment_contexts})
    store.state.counts['uncovered_pages'] = sum(row.get('uncovered_count', 1) for row in attachment_file.page_quality or []
                                               if row.get('status') == 'uncovered')
    store.state.counts['failed_pages'] = sum(row.get('status') == 'failed' and row.get('page_no') is not None
                                            for row in attachment_file.page_quality or [])
    store.state.counts['failed_files'] = sum(row.get('status') == 'failed' and row.get('page_no') is None
                                            for row in attachment_file.page_quality or [])
    store.state.counts['parsed_pages'] = sum(row.get('action') == 'ocr' and 'parse_meta' in row
                                            for row in attachment_file.page_quality or [])

    if attachment_candidates or package_amounts or any(row.package_no is None for row in html_candidates):
        store.begin('bind_packages')
        binding_audit, binding_failures = bind_packages(
            understanding, html_candidates + attachment_candidates, notice, tables, client, calls,
            attachment_contexts, package_amounts=package_amounts)
        store.write_output('bind_packages', binding_audit | {'effective_understanding': understanding.model_dump()})
        store.finish('bind_packages', 'partial' if binding_failures or binding_audit['unresolved'] else 'success')
        all_failures.extend(binding_failures)
    award_hints = winner_names(html_candidates + attachment_candidates)

    combined = html_candidates + attachment_candidates
    store.begin("normalize_candidates")
    normalized = normalize_candidates(store.run_id, combined, catalog)
    store.write_output("normalize_candidates", normalized)
    store.finish("normalize_candidates", normalized.status)
    store.state.counts["normalized_candidates"] = len(normalized.candidates)
    all_failures.extend(normalized.failures)

    store.begin("merge_candidates")
    evidence = _merge_evidence(notice, tables, understanding, index, attachment_contexts,
                               package_amounts, award_hints)
    store.write_output("merge_evidence", evidence)
    def merge_call():
        store.merge_wait("running")
        try:
            return merge_projects(store.run_id, understanding, normalized.candidates, package_amounts,
                                  evidence=evidence, llm=client, counter=calls)
        finally:
            store.merge_wait("resuming")

    store.merge_wait("queued")
    try:
        try:
            merged = yield merge_call
        finally:
            store.merge_wait(None)
    except Exception as exc:
        store.state.status = "failed"
        store.state.review_required = True
        store.finish("merge_candidates", "failed", "unexpected_error", str(exc))
        raise
    merged.unresolved.extend(_rejected_supplier_fields(html_file, understanding))
    merged.unresolved.extend(_rejected_supplier_fields(attachment_file, understanding))
    store.write_output("merge_candidates", merged)
    store.finish("merge_candidates", merged.status)

    store.begin("repair_packages")
    check_failures = repair_packages(merged, award_hints, client, calls)
    all_failures.extend(check_failures)
    merged.failures.extend(check_failures)
    store.write_output("repair_packages", merged)
    store.finish("repair_packages", "partial" if check_failures else "success")

    store.begin("complete_suppliers")
    supplier_failures = complete_suppliers(merged, client, calls)
    all_failures.extend(supplier_failures)
    merged.failures.extend(supplier_failures)
    if supplier_failures:
        merged.status = "partial"
    finalize_suppliers(merged)
    store.write_output("complete_suppliers", merged)
    store.finish("complete_suppliers", "partial" if supplier_failures else "success")
    store.state.counts["final_cobs"] = sum(len(project.cobs) for project in merged.projects)
    store.state.counts["final_subs"] = sum(len(project.subs) for project in merged.projects)
    public = [project.model_dump(exclude={"provenance"}) for project in merged.projects]
    result = {"schema_version": "announcement-v2", "announcement_id": announcement_id,
              "project_name": understanding.project_name, "source_project_no": understanding.source_project_no,
              "purchaser": understanding.purchaser, "announcement_type": understanding.announcement_type,
              "summary_amount": understanding.summary_amount.model_dump() if understanding.summary_amount else None,
              "packages": public}
    if persist:
        write_json(store.directory / "projects.json", result)
    store.projects = public
    store.result = result

    final = "success"
    if understanding.status == "failed" or not merged.projects:
        final = "failed"
    elif all_failures or merged.status == "partial" or attachment_file.status == "partial":
        final = "partial"
    store.state.status = final
    store.state.review_required = final != "success"
    return _finish(store, calls, all_failures, started)


def _first_code(failures: list[Failure]) -> str | None:
    return failures[0].failure_code if failures else None


def _rejected_supplier_fields(observations, understanding):
    """Feed failed, identifiable SUB observations to the existing default-policy block list."""
    unresolved = []
    known = {package.package_no for package in understanding.packages}
    for receipt in observations.reception:
        for rejected in receipt.get('unresolved_items', []):
            raw = rejected['raw']
            if not isinstance(raw, dict) or raw.get('entity_type') != 'sub' or not isinstance(raw.get('fields'), dict):
                continue
            name = raw['fields'].get('supplier_name')
            if isinstance(name, dict):
                name = name.get('raw_value')
            if not isinstance(name, str) or not name.strip():
                continue
            package_no = next((package.package_no for index, package in enumerate(understanding.packages)
                               if rejected['path'].startswith(f'packages[{index}].')), None)
            if package_no is None and isinstance(raw.get('package_no'), str) and raw['package_no'] in known:
                package_no = raw['package_no']
            packages = [package_no] if package_no else [package.package_no for package in understanding.packages]
            for number in packages:
                for field in ('bid_amount', 'review_status'):
                    unresolved.append({'package_no': number, 'supplier_name': name, 'field': field,
                        'reason': 'candidate_reception_failed', 'path': rejected['path'],
                        'source_ref': raw.get('source_ref')})
    return unresolved


def run_batch(announcement_ids: list[str], workers: int | None = None,
              **kwargs) -> dict[str, RunReport]:
    """Run independent chains, releasing normal case slots only while merging.

    Repeated ids run once to avoid concurrent writes to the same run directory. Even workers=1
    permits merge overlap; use run_announcement in a loop for fully sequential execution.
    """
    ids = list(dict.fromkeys(announcement_ids))
    if not ids:
        return {}
    default = int(concurrency_settings()["announcement_workers"])
    limit = max(1, int(workers) if workers is not None else default)
    async def batch():
        settings = concurrency_settings()
        with MergeScheduler(limit, int(settings["merge_workers"]), int(settings["resume_workers"])) as scheduler:
            # Drain all started chains before reporting an error, so no background work can
            # write into run directories after this function has returned.
            results = await asyncio.gather(*(scheduler.run(announcement_chain(aid, **kwargs))
                                             for aid in ids), return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                raise result
        return dict(zip(ids, results))
    return asyncio.run(batch())


def _merge_evidence(notice, tables, understanding, index, contexts, package_amounts, award_hints):
    sources = dict(notice.source_contexts)
    pages = {}
    for context in contexts:
        sources.update({segment['source_ref']: segment | {
            'file_id': context['file_id'], 'page_no': context['page_no'],
            'coordinate_kind': context['coordinate_kind']} for segment in context['source_segments']})
        key = '%s:%s' % (context['file_id'], context['page_no'])
        page = pages.setdefault(key, {'file_name': context['file_name'], 'text_head': '', 'table_headers': []})
        page['text_head'] = (page['text_head'] + ' '.join(segment['text'] for segment in context['source_segments']))[:400]
        page['table_headers'].extend(segment['headers'] for segment in context['source_segments'] if segment.get('headers'))
    observations = {}
    for package in understanding.packages:
        entries = ([package.package_amount] if package.package_amount else []) + package.amount_alternatives
        if understanding.package_mode == 'single' and understanding.summary_amount:
            entries += [understanding.summary_amount]
        observations[package.package_no] = [amount.model_dump() | {'origin': 'announcement'} for amount in entries]
    for amount in package_amounts:
        observations.setdefault(amount.package_no, []).append(amount.model_dump() | {'origin': 'attachment'})
    by_index = {table.table_index: table for table in notice.tables}
    return MergeEvidence(
        package_registry=[package_context(package) for package in understanding.packages],
        source_contexts=sources, attachment_pages=pages, amount_observations=observations,
        file_names={item.file_id: item.display_name for item in index.files} if index else {},
        html_tables={str(table.table_index): {'table_role': table.table_role, 'row_grain': table.row_grain,
            'package_scope': table.package_scope, 'headers': by_index[table.table_index].headers,
            'section': by_index[table.table_index].section, 'issues': table.issues} for table in tables.tables},
        winner_hints=award_hints)


def _first_message(failures: list[Failure]) -> str | None:
    return failures[0].failure_message if failures else None


def _finish(store: RunStore, calls: CallCounter, failures: list[Failure], started: float) -> RunReport:
    summary = Counter(item.failure_code for item in failures)
    report = RunReport(
        run_id=store.run_id,
        announcement_id=store.announcement_id,
        status=store.state.status,
        duration_ms=int((time.perf_counter() - started) * 1000),
        merge_timing=store.state.merge_timing,
        counts=store.state.counts,
        llm_calls=counts_snapshot(calls),
        failure_summary=[{"failure_code": code, "count": count} for code, count in sorted(summary.items())],
        review_required=store.state.review_required or store.state.status != "success",
    )
    report.projects = getattr(store, "projects", None)
    report.result = getattr(store, "result", None)
    report.model_calls = calls.model_calls()
    store.begin("persist_and_report")
    store.write_output("persist_and_report", report)
    store.finish("persist_and_report", "success")
    store.state.current_step = "persist_and_report"
    store.save()
    return report
