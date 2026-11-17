"""Summarize the frozen live run. No API, OCR, database, or production mutation."""
from __future__ import annotations
from collections import Counter, defaultdict
import json
from pathlib import Path
import statistics
import sys
import unicodedata

from evaluate_front_half_20261108 import ROOT, OUT, GOLD_DIR, read, write, sha, freeze
from ict.llm import parse_json_object


def ratio(n, d):
    return {"numerator": n, "denominator": d, "rate": n / d if d else None}


def norm(value):
    return "".join(unicodedata.normalize("NFKC", str(value or "")).split())


def usage_summary(rows):
    total = Counter()
    latencies = []
    for row in rows:
        result = row.get("result", row)
        total["calls"] += 1
        total["failed_calls"] += bool(row.get("error"))
        for key, value in (result.get("usage") or {}).items():
            if isinstance(value, (float, int)):
                total[key] += value
        if row.get("elapsed_s") is not None:
            latencies.append(row["elapsed_s"])
    if latencies:
        total["latency_sum_s"] = sum(latencies)
        total["latency_median_s"] = statistics.median(latencies)
    return dict(total)


def announcements(manifest):
    cases, issues, counts, tokens = [], Counter(), Counter(), []
    gold = read(GOLD_DIR / "gold_snapshot.json")
    gold_rows = {name: definition["rows"] for name, definition in gold["sheets"].items()}
    diagnostics = []
    from ict.html_context import parse_notice
    from bs4 import BeautifulSoup
    for aid in manifest["ids"]:
        folder = OUT / "announcement_runs" / aid
        a, report = read(folder / "01_announcement_understanding.json"), read(folder / "10_run_report.json")
        branches = a["branch_audit"]
        entry = {"announcement_id": aid, "status": report["status"], "duration_ms": report["duration_ms"],
                 "failures": a["failures"], "summary_accepted": bool(branches["summary"]["output"]),
                 "body_accepted": bool(branches["body"]["output"]), "final_output": (folder / "projects.json").exists()}
        cases.append(entry)
        counts["cases"] += 1
        counts["summary_accepted"] += entry["summary_accepted"]
        counts["body_accepted"] += entry["body_accepted"]
        counts["usable_final_outputs"] += entry["final_output"]
        for branch in ("summary", "body"):
            for attempt in branches[branch]["attempts"]:
                for issue in attempt["issues"]:
                    issues[branch + ":" + issue["message"]] += 1
        # A rejected model answer is diagnostic evidence, never an accepted result.
        answers = []
        for answer in branches["body"]["responses"]:
            try:
                answers.append(parse_json_object(answer))
            except ValueError:
                pass
        raw = answers[-1] if answers else {}
        raw_candidates = list(raw.get("candidates", []))
        for package in raw.get("packages", []):
            raw_candidates += package.get("candidates", [])
        notice = parse_notice(Path(manifest["html"][aid]))
        literal_source = norm(BeautifulSoup(notice.body_html, "lxml").get_text(" ", strip=True))
        for field in ("项目名称", "采购单位"):
            expected = list(dict.fromkeys(r["values"][field] for r in gold_rows["总表"] if r["values"]["UUID"] == aid))
            key = {"项目名称": "project_name", "采购单位": "purchaser"}[field]
            diagnostics.append({"announcement_id": aid, "field": field, "eligible": True,
                "expected": expected, "raw_value": raw.get(key), "exact": any(norm(raw.get(key)) == norm(v) for v in expected),
                "scope": "rejected_body_answer_case_identity"})
        for sheet, label, entity, field in (("标的表", "产品服务名称", "cob", "object_name"),
                                             ("投标人表", "名称", "sub", "supplier_name")):
            names = [norm(r.get("fields", {}).get(field)) for r in raw_candidates if r.get("entity_type") == entity]
            for row in gold_rows[sheet]:
                if row["values"]["UUID"] != aid:
                    continue
                expected = row["values"].get(label)
                if not norm(expected):
                    continue
                diagnostics.append({"announcement_id": aid, "sheet": sheet, "gold_row": row["row"], "field": label,
                    "expected": expected, "eligible": norm(expected) in literal_source,
                    "exact": norm(expected) in names,
                    "scope": "literal_HTML_gold_name_recall_in_rejected_body_answer_ignores_package_identity"})
        tokens.extend(read(path) for path in (OUT / "calls/announcements" / aid).glob("*.json"))
    diagnostic_summary = {}
    for field in ("项目名称", "采购单位", "产品服务名称", "名称"):
        selected = [r for r in diagnostics if r["field"] == field and r["eligible"]]
        diagnostic_summary[field] = ratio(sum(r["exact"] for r in selected), len(selected))
    durations = [r["duration_ms"] / 1000 for r in cases]
    result = {"counts": dict(counts), "rejection_messages_by_attempt": dict(issues), "usage": usage_summary(tokens),
              "latency_median_s": statistics.median(durations), "latency_sum_s": sum(durations),
              "latency_max_s": max(durations), "batch": read(OUT / "announcement_batch.json"),
              "rejected_raw_answer_diagnostics": diagnostic_summary, "cases": cases,
              "warning": "Raw answer diagnostics are not successful extraction or final three-table scores. Literal source names are a narrow subset; package ownership, field correctness and post-processing are not credited."}
    write(OUT / "announcement_diagnostics.json", diagnostics)
    write(OUT / "announcement_summary.json", result)
    return result


def page_metrics(rows):
    gate = [r for r in rows if r["gate"]["method"] == "current_inspect_page"]
    judged = [r for r in gate if type(r["label"].get("need_useful")) is bool]
    relevant = [r for r in judged if r["label"]["need_useful"]]
    positive = [r for r in judged if r["gate"]["action"] in ("ocr", "extract")]
    ocr = [r for r in judged if r["gate"]["action"] == "ocr"]
    useful = [r for r in rows if r["label"].get("need_useful") is True]
    def readable(row):
        return row["gate"]["action"] == "ocr" or (row["gate"]["action"] == "extract" and row["label"].get("native_sufficient") is True)
    return {"units": len(rows), "need_useful": len(useful), "image_gated": len(gate),
        "image_gate_actions": dict(Counter(r["gate"]["action"] for r in gate)),
        "image_gate_reference_uncertain": len(gate) - len(judged),
        "image_gate_retention_recall": ratio(sum(r["gate"]["action"] in ("ocr", "extract") for r in relevant), len(relevant)),
        "image_gate_readable_recall": ratio(sum(readable(r) for r in relevant), len(relevant)),
        "image_gate_keep_precision": ratio(sum(r["label"]["need_useful"] for r in positive), len(positive)),
        "image_gate_skip_useful": sum(r["gate"]["action"] == "skip" for r in relevant),
        "image_gate_extract_missing_image_data": sum(r["gate"]["action"] == "extract" and r["label"].get("native_sufficient") is False for r in relevant),
        "ocr_selected": len(ocr),
        "ocr_no_incremental_information": ratio(sum(not r["label"]["need_useful"] for r in ocr), len(ocr)),
        "ocr_native_already_sufficient": sum(r["label"]["need_useful"] and r["label"].get("native_sufficient") is True for r in ocr),
        "native_direct_useful": sum(r["gate"]["method"] == "native_direct" and r["label"].get("need_useful") is True for r in rows),
        "native_direct_no_incremental_information": sum(r["gate"]["method"] == "native_direct" and r["label"].get("need_useful") is False for r in rows),
        "read_budget_outside_useful": sum(r["gate"]["method"] == "read_page_budget" and r["label"].get("need_useful") is True for r in rows),
        "hard_ignore_useful": sum(r["gate"]["method"] == "rule_name_ignore" and r["label"].get("need_useful") is True for r in rows),
        "readable_before_extraction_budget": ratio(sum(readable(r) for r in useful), len(useful)),
        "readable_after_file_budget": ratio(sum(readable(r) and r["within_file_extraction_budget"] for r in useful), len(useful))}


def pages():
    manifest = read(OUT / "page_manifest.json")
    overrides_path = OUT / "source_review.json"
    reviews = read(overrides_path) if overrides_path.exists() else {"reviews": []}
    overrides = {r["page_key"]: r for r in reviews["reviews"] if r.get("override")}
    rows = []
    for page in manifest["pages"]:
        gate = read(OUT / "page_gates" / (page["page_key"] + ".json"))
        reference = read(OUT / "page_references" / (page["page_key"] + ".json"))
        label = reference.get("output", {})
        raw_label = dict(label)
        if page["page_key"] in overrides:
            label = label | overrides[page["page_key"]]["override"]
        rows.append(page | {"gate": gate, "raw_label": raw_label, "label": label,
                           "reference_error": reference.get("error"), "within_file_extraction_budget": False})
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["file_key"]].append(row)
    # Reproduce the existing two-stage per-file cap, preserving source page order.
    # OCR output chunk count is unknown without OCR; those pages are only an eligibility ceiling.
    for group in grouped.values():
        selected = [r for r in group if r["gate"]["action"] in ("extract", "ocr")][:30]
        remaining = 30
        for row in selected:
            needed = row["extraction_context_count"]
            row["within_file_extraction_budget"] = remaining >= needed
            remaining -= min(remaining, needed)
    by_file = []
    for key, group in grouped.items():
        by_file.append({"file_key": key, "announcement_id": group[0]["announcement_id"],
            "file_name": group[0]["file_name"], "stratum": group[0]["stratum"],
            "metrics": page_metrics(group)})
    raw_rows = [r | {"label": r["raw_label"]} for r in rows]
    calls = [read(f) for f in (OUT / "calls/page_gates").glob("*/*.json")]
    refs = [read(f) for f in (OUT / "page_references").glob("*.json")]
    result = {"file_count": len(by_file), "announcement_count": len({r["announcement_id"] for r in rows}),
        "strata": dict(Counter(r["stratum"] for r in rows)),
        "preparation_failures": manifest["preparation_failures"], "metrics_raw_reference": page_metrics(raw_rows),
        "metrics_source_reviewed": page_metrics(rows), "source_review_overrides": len(overrides),
        "gate_usage": usage_summary(calls), "reference_usage": usage_summary(refs), "by_file": by_file,
        "warnings": ["Conditional component test: historical HTML candidates, current fixed needs, current exact inspect_page API.",
                     "Structural file-stratum sample, not a random sample of pages or announcements; unweighted rates do not estimate corpus accuracy.",
                     "Reference uses the same model with a separate blinded prompt and higher-resolution original page; this is not human gold.",
                     "Only five outside-budget pages sampled per long file; remaining uninspected pages are not treated as negatives.",
                     "No OCR calls: readable coverage is eligibility, not extraction success. OCR chunk counts and actual OCR performance are unknown.",
                     "File-cap simulation excludes the 100-context announcement cap because other files in each announcement were not evaluated."]}
    write(OUT / "page_analysis.json", rows)
    write(OUT / "page_summary.json", result)
    return result


def verify(manifest):
    gold = read(GOLD_DIR / "gold_snapshot.json")
    checks = {"production": all(sha(ROOT / name) == digest for name, digest in manifest["source_sha256"].items()),
              "html": all(sha(manifest["html"][aid]) == digest for aid, digest in manifest["html_sha256"].items()),
              "gold": sha(GOLD_DIR / "gold_snapshot.json") == manifest["gold_sha256"],
              "original_gold_workbook": sha(gold["source"]) == gold["sha256"]}
    selected = read(OUT / "attachment_selection.json")["files"]
    checks["selected_attachments"] = all(sha(row["path"]) == row["digest"] for row in selected)
    historical = {}
    for path in (OUT / "reference_contexts").glob("*.json"):
        historical.update(read(path)["source_sha256"])
    checks["historical_contexts"] = all(sha(path) == digest for path, digest in historical.items())
    checks["files_checked"] = {"production": len(manifest["source_sha256"]), "html": len(manifest["html_sha256"]),
                              "gold": 1, "original_gold_workbook": 1, "selected_attachments": len(selected), "historical_contexts": len(historical)}
    write(OUT / "integrity.json", checks)
    assert all(checks[key] for key in ("production", "html", "gold", "original_gold_workbook", "selected_attachments", "historical_contexts"))
    return checks


if __name__ == "__main__":
    frozen = freeze()
    a, p = announcements(frozen), pages()
    verify(frozen)
    print(json.dumps({"announcements": a["counts"], "pages": p["metrics_source_reviewed"],
                      "raw_answer_diagnostics": a["rejected_raw_answer_diagnostics"]}, ensure_ascii=True, indent=2))
