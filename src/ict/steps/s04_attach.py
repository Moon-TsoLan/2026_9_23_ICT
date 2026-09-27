"""Steps 4-6. The model selects files and pages, then extracts only those pages."""

from __future__ import annotations

import json
import re
from pathlib import Path

from ict.candidates import seal_candidate
from ict.config import (
    FILE_CLASS_PRIORITY,
    FILE_PROBE_PAGES,
    FILE_TEXT_HEAD,
    MAX_FILES_PER_PROJECT,
    MAX_PAGES_PER_ANNOUNCEMENT,
    MAX_PAGES_PER_FILE,
    PAGE_TEXT_HEAD,
    SOURCE_PRIORITY,
)
from ict.documents import MarkdownUnavailable, read_document
from ict.ids import make_project_id
from ict.llm import LLMClient, LLMError, complete_json
from ict.schemas import (
    AttachmentIndex,
    Candidate,
    Failure,
    FileDecision,
    FileDecisions,
    PageDecision,
    PageDecisions,
    ProjectPlans,
)

FILE_PACKAGE_RE = re.compile(r"(?:标\s*包|采购包|合同包|第)?\s*([0-9]+|[A-Za-z])\s*包|(?:标\s*包|采购包|合同包)\s*[:：]?\s*([0-9]+|[A-Za-z])")


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


FILE_CLASSES = {
    "award_detail",
    "bid_quote",
    "winner_detail",
    "tender_requirement",
    "qualification",
    "contract",
    "evaluation",
    "unrelated",
    "unknown",
}
READ_STRATEGIES = {"skip", "target_pages", "unsupported"}
CLASS_PRIORITY = {
    "award_detail": 100,
    "bid_quote": 90,
    "winner_detail": 80,
    "evaluation": 70,
    "tender_requirement": 40,
    "qualification": 30,
    "contract": 30,
    "unrelated": 30,
    "unknown": 30,
}


def _validate_files(parsed: dict) -> str:
    if not isinstance(parsed.get("file_decisions"), list):
        return "需要 file_decisions 数组"
    for item in parsed["file_decisions"]:
        if item.get("file_class") not in FILE_CLASSES:
            return "file_class 不在允许值内"
        if item.get("read_strategy") not in READ_STRATEGIES:
            return "read_strategy 不在允许值内"
    return ""


def _validate_pages(parsed: dict) -> str:
    if not isinstance(parsed.get("page_decisions"), list):
        return "需要 page_decisions 数组"
    for item in parsed["page_decisions"]:
        if item.get("extraction_mode") not in {None, "", "text", "table", "text_and_table", "unsupported"}:
            return "extraction_mode 不在允许值内"
    return ""


READ_FAILURE = {
    "low_text": "scanned_or_low_text_pdf",
    "unsupported": "unsupported_image",
    "encrypted": "encrypted_pdf",
    "parse_failed": "document_parse_failed",
}


def _file_path(index: AttachmentIndex, file_id: str) -> Path | None:
    if not index.attachment_directory:
        return None
    root = Path(index.attachment_directory)
    for item in index.files:
        if item.file_id == file_id:
            return root / item.relative_path
    return None


def _probe(index: AttachmentIndex, file_id: str) -> dict:
    path = _file_path(index, file_id)
    meta = next(item for item in index.files if item.file_id == file_id)
    if path is None or meta.readability != "text_extractable":
        return {"file_id": file_id, "display_name": meta.display_name, "readability": meta.readability, "first_pages": []}
    try:
        pages = read_document(path, list(range(1, FILE_PROBE_PAGES + 1)))
    except MarkdownUnavailable as exc:
        return {
            "file_id": file_id,
            "display_name": meta.display_name,
            "readability": "unknown",
            "failure": exc.reason,
            "first_pages": [],
        }
    except Exception:
        return {"file_id": file_id, "display_name": meta.display_name, "readability": "parse_failed", "first_pages": []}
    return {
        "file_id": file_id,
        "display_name": meta.display_name,
        "page_count": meta.page_count,
        "text_density": meta.text_density,
        "readability": meta.readability,
        "first_pages": [
            {
                "page_no": page["page_no"],
                "text_head": page["text"][:FILE_TEXT_HEAD],
                "table_headers": [table["headers"] for table in page["tables"]],
            }
            for page in pages
        ],
    }


def triage_files(run_id: str, plans: ProjectPlans, index: AttachmentIndex | None, llm: LLMClient | None, counter=None) -> FileDecisions:
    failures: list[Failure] = []
    if index is None or index.attachment_directory is None:
        failures.append(Failure(failure_code="attachment_index_miss", failure_message="附件索引不存在"))
        return FileDecisions(run_id=run_id, status="skipped", failures=failures)
    if not any(project.needs_attachment for project in plans.projects):
        return FileDecisions(run_id=run_id, status="skipped", failures=failures)
    decisions: list[FileDecision] = []
    readable = []
    for item in index.files:
        hint = (item.model_extra or {}).get("failure_hint")
        if item.readability == "text_extractable":
            readable.append(item)
            continue
        code = READ_FAILURE.get(hint or item.readability, "document_parse_failed")
        decisions.append(
            FileDecision(
                file_id=item.file_id,
                file_class="unknown",
                priority=0,
                read_strategy="unsupported",
                reason=item.readability,
                failure_code=code,
                failure_message=hint,
            )
        )
        failures.append(Failure(failure_code=code, failure_message=item.display_name, location=item.file_id))
    if llm is None:
        failures.append(Failure(failure_code="llm_call_failed", failure_message="未配置 DeepSeek"))
        return FileDecisions(run_id=run_id, status="failed", file_decisions=decisions, failures=failures)
    probes = [_probe(index, item.file_id) for item in readable]
    try:
        parsed = complete_json(
            llm,
            step="triage_files",
            prompt_version="triage-files-v1",
            user=json.dumps(
                {"plans": [project.model_dump() for project in plans.projects], "files": probes},
                ensure_ascii=False,
            ),
            validate=_validate_files,
            counter=counter,
        )
    except (LLMError, ValueError) as exc:
        code = "llm_call_failed" if isinstance(exc, LLMError) else "llm_schema_invalid"
        failures.append(Failure(failure_code=code, failure_message=str(exc)))
        return FileDecisions(run_id=run_id, status="failed", file_decisions=decisions, failures=failures)
    for item in parsed.get("file_decisions") or []:
        if item.get("file_id") not in {file.file_id for file in readable}:
            continue
        strategy = item.get("read_strategy") or "skip"
        if strategy not in {"skip", "target_pages", "unsupported"}:
            strategy = "skip"
        decisions.append(
            FileDecision(
                file_id=item["file_id"],
                file_class=item.get("file_class") or "unknown",
                expected_fields=item.get("expected_fields") or [],
                possible_packages=[str(value) for value in item.get("possible_packages") or []],
                priority=float(item.get("priority") or 0),
                read_strategy=strategy,
                reason=item.get("reason") or "",
            )
        )
    selected = [item for item in decisions if item.read_strategy == "target_pages"]
    selected.sort(key=lambda item: (item.priority, item.file_class in FILE_CLASS_PRIORITY), reverse=True)
    keep = {item.file_id for item in selected[: MAX_FILES_PER_PROJECT * max(1, len(plans.projects))]}
    for item in decisions:
        if item.read_strategy == "target_pages" and item.file_id not in keep:
            item.read_strategy = "skip"
            item.reason = "超过每个项目的文件上限"
    status = "partial" if failures else "success"
    return FileDecisions(run_id=run_id, status=status, file_decisions=decisions, failures=failures)


def locate_pages(run_id: str, plans: ProjectPlans, files: FileDecisions, index: AttachmentIndex, llm: LLMClient | None, counter=None) -> PageDecisions:
    chosen = [item for item in files.file_decisions if item.read_strategy == "target_pages"]
    if not chosen:
        return PageDecisions(run_id=run_id, status="skipped", failures=list(files.failures))
    if llm is None:
        return PageDecisions(
            run_id=run_id,
            status="failed",
            failures=[Failure(failure_code="llm_call_failed", failure_message="未配置 DeepSeek")],
        )
    page_inputs = []
    failures: list[Failure] = []
    for decision in chosen:
        path = _file_path(index, decision.file_id)
        if path is None:
            continue
        try:
            pages = read_document(path)
        except MarkdownUnavailable as exc:
            failures.append(
                Failure(
                    failure_code="no_text_extractable",
                    failure_message=f"unknown:{exc.reason}",
                    location=decision.file_id,
                )
            )
            continue
        except Exception as exc:
            failures.append(
                Failure(failure_code="unexpected_error", failure_message=str(exc), location=decision.file_id)
            )
            continue
        slim = []
        for page in pages:
            headers = [table["headers"] for table in page["tables"]]
            if page["chars"] < 20 and not headers:
                continue
            slim.append(
                {
                    "page_no": page["page_no"],
                    "source": "markdown",
                    "text_head": page["text"][:PAGE_TEXT_HEAD],
                    "table_headers": headers,
                }
            )
        page_inputs.append(
            {
                "file_id": decision.file_id,
                "display_name": path.name,
                "possible_packages": decision.possible_packages,
                "pages": slim[:80],
            }
        )
    queries = [query for project in plans.projects for query in project.search_queries]
    known_packages = [project.package_no for project in plans.projects]
    file_meta = {item["file_id"]: item for item in page_inputs}
    try:
        parsed = complete_json(
            llm,
            step="locate_pages",
            prompt_version="locate-pages-v1",
            user=json.dumps(
                {"known_packages": known_packages, "search_queries": queries, "files": page_inputs},
                ensure_ascii=False,
            ),
            validate=_validate_pages,
            counter=counter,
        )
    except (LLMError, ValueError) as exc:
        code = "llm_call_failed" if isinstance(exc, LLMError) else "llm_schema_invalid"
        return PageDecisions(run_id=run_id, status="failed", failures=[*failures, Failure(failure_code=code, failure_message=str(exc))])
    decisions: list[PageDecision] = []
    per_file: dict[str, int] = {}
    for item in parsed.get("page_decisions") or []:
        file_id = item.get("file_id")
        per_file[file_id] = per_file.get(file_id, 0) + 1
        if per_file[file_id] > MAX_PAGES_PER_FILE or len(decisions) >= MAX_PAGES_PER_ANNOUNCEMENT:
            continue
        mode = item.get("extraction_mode") or "text"
        if mode not in {"text", "table", "text_and_table", "unsupported"}:
            mode = "text"
        meta = file_meta.get(file_id) or {}
        decisions.append(
            PageDecision(
                file_id=file_id,
                page_no=int(item.get("page_no") or 0),
                relevance=float(item.get("relevance") or 0),
                expected_fields=item.get("expected_fields") or [],
                package_scope=coerce_package_scope(
                    str(item.get("package_scope") or "unknown"),
                    list(meta.get("possible_packages") or []),
                    known_packages,
                    str(meta.get("display_name") or ""),
                ),
                extraction_mode=mode,
                reason=item.get("reason") or "",
            )
        )
    status = "partial" if failures or not decisions else "success"
    return PageDecisions(run_id=run_id, status=status, page_decisions=decisions, failures=failures)


def extract_attachment_candidates(
    run_id: str,
    project_name: str,
    plans: ProjectPlans,
    pages: PageDecisions,
    index: AttachmentIndex,
    llm: LLMClient | None,
    seq_start: int,
    counter=None,
    file_classes: dict[str, str] | None = None,
) -> tuple[list[Candidate], list[Failure], list[dict], int]:
    failures: list[Failure] = []
    candidates: list[Candidate] = []
    quality: list[dict] = []
    seq = seq_start
    if not pages.page_decisions:
        return candidates, failures, quality, seq
    if llm is None:
        failures.append(Failure(failure_code="llm_call_failed", failure_message="未配置 DeepSeek"))
        return candidates, failures, quality, seq
    by_package: dict[str, list] = {}
    for decision in pages.page_decisions:
        if decision.extraction_mode == "unsupported":
            continue
        path = _file_path(index, decision.file_id)
        if path is None:
            continue
        try:
            loaded = read_document(path, [decision.page_no])
        except Exception as exc:
            quality.append(
                {"file_id": decision.file_id, "page_no": decision.page_no, "readability": "parse_failed", "failure_code": "document_parse_failed"}
            )
            failures.append(Failure(failure_code="document_parse_failed", failure_message=str(exc), location=decision.file_id))
            continue
        if not loaded or loaded[0]["chars"] < 20:
            quality.append(
                {
                    "file_id": decision.file_id,
                    "page_no": decision.page_no,
                    "readability": "low_text",
                    "failure_code": "scanned_or_low_text_pdf",
                }
            )
            continue
        quality.append(
            {"file_id": decision.file_id, "page_no": decision.page_no, "readability": "text_extractable", "failure_code": None}
        )
        by_package.setdefault(decision.package_scope, []).append(
            {
                "file_id": decision.file_id,
                "file_name": path.name,
                "page_no": decision.page_no,
                "text": loaded[0]["text"][:6000],
                "tables": loaded[0]["tables"][:4],
            }
        )
    plans_by_no = {project.package_no: project for project in plans.projects}
    for package_no, contexts in by_package.items():
        plan = plans_by_no.get(package_no)
        if len(contexts) > 12:
            failures.append(
                Failure(
                    failure_code="page_context_truncated",
                    failure_message=f"包 {package_no} 超出 12 页，未送入 {len(contexts) - 12} 页",
                    location=str(package_no),
                )
            )
        payload = {
            "current_package": {
                "project_id": None if plan is None else plan.project_id,
                "package_no": None if package_no in {"unknown", "announcement"} else package_no,
                "missing_fields": [] if plan is None else plan.missing_fields,
            },
            "page_contexts": contexts[:12],
        }
        try:
            parsed = complete_json(
                llm,
                step="extract_attachment_candidates",
                prompt_version="attachment-extract-v1",
                user=json.dumps(payload, ensure_ascii=False),
                validate=lambda item: "" if isinstance(item.get("candidates"), list) else "需要 candidates 数组",
                counter=counter,
            )
        except (LLMError, ValueError) as exc:
            code = "llm_call_failed" if isinstance(exc, LLMError) else "llm_schema_invalid"
            failures.append(Failure(failure_code=code, failure_message=str(exc), location=package_no))
            continue
        for item in parsed.get("candidates") or []:
            entity = item.get("entity_type")
            if entity not in {"cob", "sub"}:
                continue
            file_name = next((context["file_name"] for context in contexts if context["file_id"] == item.get("file_id")), contexts[0]["file_name"] if contexts else "")
            item_package = item.get("package_no") or payload["current_package"]["package_no"] or _package_from_filename(file_name)
            project_id = make_project_id(project_name, str(item_package)) if item_package and project_name else None
            source_type = "pdf"
            file_id = item.get("file_id") or (contexts[0]["file_id"] if contexts else None)
            file_class = (file_classes or {}).get(file_id or "", "bid_quote")
            candidates.append(
                seal_candidate(
                    candidate_id=f"cand_{seq:06d}",
                    entity_type=entity,
                    project_id=project_id,
                    package_no=None if item_package is None else str(item_package),
                    source_type=source_type,
                    file_id=file_id,
                    source_priority=CLASS_PRIORITY.get(file_class, 90),
                    raw_fields=item.get("fields") or {},
                    issues=item.get("issues") or [],
                    source_class=file_class,
                )
            )
            seq += 1
    return candidates, failures, quality, seq
