"""Stage entry points for the attachment redesign, independently replayable from JSON."""
from dataclasses import asdict
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import re

import pymupdf
from bs4 import BeautifulSoup

from ict.config import ATTACHMENTS_ROOT, DATA_HTML
from ict.file_routing import classify, compact, read_file
from ict.html_context import parse_notice
from ict.attachment_pages import office_pdf
from ict.parse.census import SKIP_NAMES, sniff
from ict.money import parse_price_cell
from ict.steps.s01_understand import read_announcement
from ict.steps.s03_plan import plan_projects


def reference_tables(pdf, part):
    """Read the grid on pages with a relevant column. Never fabricate cells from spacing."""
    if part.tables or part.kind != "pdf_page":
        return part.tables
    text = compact(part.text)
    if not re.search(r"包名称|包名|标段名称|标项名称|品目|规格|型号|数量", text):
        return []
    tables = []
    for table in pdf[part.number - 1].find_tables().tables:
        rows = table.extract()
        if rows:
            tables.append({"headers": rows[0], "rows": rows[1:]})
    return tables


def source_parts(doc, result):
    if result["route"] not in {"direct", "reference"}:
        return []
    if result["route"] == "reference" and any(p.kind == "pdf_page" for p in doc.parts):
        with ExitStack() as stack:
            try:
                path = doc.path
                if sniff(path)[0] == "ole":
                    path = office_pdf(path, stack.enter_context(TemporaryDirectory()))
                pdf = stack.enter_context(pymupdf.open(path))
            except (OSError, RuntimeError, ValueError) as exc:
                result["errors"].append(f"table source: {exc}")
            else:
                for part in doc.parts:
                    try:
                        part.tables = reference_tables(pdf, part)
                    except (RuntimeError, ValueError, OSError) as exc:
                        result["errors"].append(f"table {part.number}: {exc}")
    return [asdict(part) for part in doc.parts]


def pending_units(candidates, notice):
    """Keep a bidder and its explicit attachment-pointer row on the same pending branch."""
    pending = [c for c in candidates if c.package_no is None]
    groups = {c.candidate_id: [c.candidate_id] for c in pending}
    soup = BeautifulSoup(notice.body_html, "lxml")
    for cob in pending:
        if cob.entity_type != "cob" or not cob.evidence:
            continue
        if cob.fields["object_name"].status != "points_to_attachment" and cob.fields["object_name"].raw_value != "详见附件":
            continue
        rows = (soup.find("tr", attrs={"data-source-ref": ref}) for ref in cob.evidence.source_refs)
        row = next((item for item in rows if item is not None), None)
        if row is None or cob.evidence.table_index is None or not 0 <= cob.evidence.table_index < len(notice.tables):
            continue
        headers = notice.tables[cob.evidence.table_index].headers
        cells = [cell.get_text(strip=True) for cell in row.find_all(["td", "th"], recursive=False)]
        owner = next((v for k, v in zip(headers, cells) if k in ("供应商", "供应商名称")), None)
        total = parse_price_cell(cob.fields["total_price"].raw_value, cob.evidence.column_units.get("total_price"))
        matches = []
        for sub in pending:
            if sub.entity_type != "sub" or not owner or sub.fields["supplier_name"].raw_value != owner:
                continue
            award = sub.fields.get("supplier_award_amount")
            parsed = parse_price_cell(award.raw_value if award else None,
                                      sub.evidence.column_units.get("supplier_award_amount") if sub.evidence else None)
            if total["value"] is not None and total["value"] == parsed["value"] and not (parsed["ambiguous"] or total["ambiguous"]):
                matches.append(sub.candidate_id)
        if len(matches) == 1:
            groups[matches[0]].extend(groups.pop(cob.candidate_id))
    return [{"ref": "virtual:" + key, "candidate_ids": members} for key, members in groups.items()]


def freeze_front(announcement_id, llm, html_dir=DATA_HTML, attachments_root=ATTACHMENTS_ROOT, *, ocr_by_file=None):
    notice = parse_notice(Path(html_dir) / (announcement_id + ".html"))
    identity, _, html = read_announcement(announcement_id, notice, llm, provisional=True)
    if identity.status == "failed":
        raise ValueError("HTML未生成可用观测: " + str(identity.failures))
    files = []
    directory = Path(attachments_root) / announcement_id
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path.name in SKIP_NAMES:
            continue
        doc = read_file(path)
        result = classify(doc)
        relative = path.relative_to(directory).as_posix()
        if result["route"] == "needs_ocr" and relative in (ocr_by_file or {}):
            recognized = {int(k): v for k, v in ocr_by_file[relative].items()}
            result = classify(doc, recognized)
            for part in doc.parts:
                if part.number in recognized:
                    part.text += "\n" + recognized[part.number]
        files.append({"path": relative, **result, "parts": source_parts(doc, result)})

    units = [dict(p.model_dump(), candidate_ids=[]) for p in identity.packages]
    by_number = {u["package_no"]: u for u in units}
    virtual = pending_units(html.candidates, notice)
    observations = []
    for c in html.candidates:
        observations.append(c.model_dump(exclude_none=True))
        if c.package_no in by_number:
            by_number[c.package_no]["candidate_ids"].append(c.candidate_id)
    needs = plan_projects(announcement_id, identity, html.candidates, bool(files))
    return {"announcement_id": announcement_id,
            "identity": identity.model_dump(exclude={"branch_audit", "packages"}),
            "html_conflicts": identity.branch_audit["conflicts"],
            "units": units, "virtual_units": virtual, "candidates": observations,
            "html_sources": notice.source_contexts, "needs": needs.model_dump(),
            "files": files}


def save_new(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)
    with path.open("x", encoding="utf-8") as out:
        out.write(serialized + "\n")


def merge_package_objects(tree, llm=None):
    """Bridge resolved branches to the existing normalization and package delta entry points."""
    from ict.catalog import Catalog
    from ict.schemas import AnnouncementUnderstanding, Candidate, MergeEvidence
    from ict.steps.s07_normalize import normalize_candidates
    from ict.steps.s08_merge import merge_projects

    run_id = tree["announcement_id"]
    packages = [{k: v for k, v in unit.items() if k != "candidate_ids"} for unit in tree["units"]]
    identity = AnnouncementUnderstanding.model_validate(dict(tree["identity"], packages=packages))
    assigned = {cid for u in tree["units"] for cid in u["candidate_ids"]}
    candidates = [Candidate.model_validate(c) for c in tree["candidates"] if c["candidate_id"] in assigned]
    native = normalize_candidates(run_id, candidates, Catalog())
    sources = dict(tree["html_sources"])
    for attachment in tree["files"]:
        if attachment["route"] == "direct":
            for part in attachment["parts"]:
                sources[f"{attachment['path']}#{part['number']}"] = {
                    "text": part["text"], "file_name": attachment["path"], "page_no": part["number"],
                    "coordinate_kind": part["kind"]}
    evidence = MergeEvidence(source_contexts=sources, package_registry=packages)
    return merge_projects(run_id, identity, native.candidates, evidence=evidence, llm=llm)

