"""Offline comparison, including color policy and a narrow literal-name diagnostic."""
from collections import Counter
import json
from pathlib import Path
import statistics
import sys
import unicodedata

from bs4 import BeautifulSoup
from openpyxl.utils import get_column_letter
from evaluate_front_half_20261108 import ROOT, read, write, sha, load_score, OUT as ORIGINAL
from ict.html_context import parse_notice
from ict.llm import load_prompt

OUT = ROOT / "work/front-half-repair-20261108"


def norm(value):
    return "".join(unicodedata.normalize("NFKC", str(value or "")).split()).casefold()


def ratio(a, b):
    return {"numerator": a, "denominator": b, "rate": a / b if b else None}


def policy_metrics(cells):
    counts = Counter(row["status"] for row in cells)
    tp, tn, exempt = counts["correct_nonempty"], counts["correct_empty"], counts["allowed_empty_inferred"]
    fp = sum(row["status"] in ("wrong_value", "overfill_empty_gold", "extra_row") and row["result"] not in (None, "") for row in cells)
    fn = sum(row["status"] in ("wrong_value", "missing_value", "missing_row") and row["gold"] not in (None, "") for row in cells)
    return {"statuses": dict(counts), "gold_slots": sum(row["status"] != "extra_row" for row in cells),
        "accuracy_gold_slots": ratio(tp + tn + exempt, sum(row["status"] != "extra_row" for row in cells)),
        "correct_nonempty": tp, "correct_empty": tn, "allowed_empty_inferred": exempt,
        "false_positive": fp, "false_negative": fn, "precision_including_extra_rows": ratio(tp, tp + fp),
        "correct_nonempty_recall": ratio(tp, tp + fn)}


def analyze_version(directory, manifest, gold):
    scorer = load_score()
    results, sheets, _ = scorer.load_results(directory / "runs", manifest["ids"])
    raw_cells, alignment, review = [], {}, []
    for name, definition in gold["sheets"].items():
        cells, rows, suggestions = scorer.cells_for(name, definition["rows"], sheets[name], definition["blue_headers"], [])
        raw_cells += cells
        alignment[name] = {key: value for key, value in rows.items() if not key.startswith("unmatched")}
        review += suggestions
    filled = {}
    for name, definition in gold["sheets"].items():
        columns = {header: get_column_letter(index + 1) for index, header in enumerate(definition["headers"])}
        for row in definition["rows"]:
            for field in definition["blue_headers"]:
                filled[(name, row["row"], field)] = columns[field] in row["filled_cells"]
    policy_cells = []
    for row in raw_cells:
        cell = dict(row)
        cell["inferred_gold"] = filled.get((row["sheet"], row["gold_row"], row["field"]), False)
        if (cell["inferred_gold"] and cell["result"] in (None, "") and cell["gold"] not in (None, "")
                and cell["status"] == "missing_value"):
            cell["status"] = "allowed_empty_inferred"
        policy_cells.append(cell)
    names, cases, usage, calls = [], [], Counter(), Counter()
    for aid in manifest["ids"]:
        folder = directory / "runs" / aid
        if not (folder / "10_run_report.json").exists():
            continue
        a, report = read(folder / "01_announcement_understanding.json"), read(folder / "10_run_report.json")
        b = a["branch_audit"]["body"].get("output", {})
        candidates = [row for group in b.get("packages", []) + [b] for row in group.get("candidates", [])]
        notice = parse_notice(Path(manifest["html"][aid]))
        source = norm(BeautifulSoup(notice.body_html, "lxml").get_text(" ", strip=True))
        def raw(value):
            return value.get("raw_value") if isinstance(value, dict) else value
        for sheet, field, key, entity in (("标的表", "产品服务名称", "object_name", "cob"), ("投标人表", "名称", "supplier_name", "sub")):
            output = {norm(raw(row["fields"].get(key))) for row in candidates if row["entity_type"] == entity}
            for row in gold["sheets"][sheet]["rows"]:
                value = row["values"].get(field)
                if row["values"]["UUID"] == aid and norm(value) and norm(value) in source:
                    names.append({"announcement_id": aid, "sheet": sheet, "field": field, "gold_row": row["row"],
                                  "gold": value, "same_name_candidate": norm(value) in output})
        for branch in ("summary", "body"):
            for call in a["branch_audit"][branch]["calls"]:
                calls[branch] += 1
                for key, value in call["usage"].items():
                    if isinstance(value, (int, float)):
                        usage[branch + "_" + key] += value
        cases.append({"announcement_id": aid, "announcement_status": a["status"], "status": report["status"],
            "output": (folder / "projects.json").exists(), "duration_ms": report["duration_ms"], "counts": report["counts"],
            "failures": report["failure_summary"], "branch_attempts": {branch: a["branch_audit"][branch]["attempts"] for branch in ("summary", "body")}})
    assert len(cases) == 50, "Do not score an unfinished batch"
    prefix = OUT / (directory.name + "_analysis")
    write(prefix.with_suffix(".json"), {"policy_cells": policy_cells, "literal_names": names, "identity_reviews": review, "cases": cases})
    return {"outputs": len(results), "announcement_status": dict(Counter(row["announcement_status"] for row in cases)),
        "output_status": dict(Counter(row["status"] for row in cases)), "alignment": alignment,
        "all_case_gold_policy_by_sheet": {name: policy_metrics([row for row in policy_cells if row["sheet"] == name]) for name in gold["sheets"]},
        "all_case_gold_policy_by_field": {name + "/" + field: policy_metrics([row for row in policy_cells if row["sheet"] == name and row["field"] == field])
                                        for name, definition in gold["sheets"].items() for field in definition["blue_headers"]},
        "literal_HTML_name_diagnostic": {name: ratio(sum(row["same_name_candidate"] for row in names if row["sheet"] == name),
                                                    sum(row["sheet"] == name for row in names)) for name in ("标的表", "投标人表")},
        "front_calls": dict(calls), "front_usage": dict(usage), "case_median_s": statistics.median(row["duration_ms"] for row in cases) / 1000,
        "warnings": ["Full-case gold includes attachment-only fields and is not the denominator of announcement-only accuracy.",
                     "Literal name diagnostics ignore package/role/field correctness; they are not table accuracy.",
                     "Color policy allows blank for colored cells, but never fabricates missing entity rows or counts accepted blanks as extracted values."]}


def main():
    current = OUT / "live-screen"
    manifest = read(current / "manifest.json")
    gold_path = ROOT / "work/e2e-20261017-replays/revised-evaluation/gold_snapshot.json"
    gold = read(gold_path)
    assert sha(gold["source"]) == gold["sha256"]
    result = {name: analyze_version(OUT / name, manifest, gold) for name in ("live", "live-compact", "live-capacity")}
    write(OUT / "comparison.json", result)
    baseline = read(OUT / "baseline.json")
    replay = read(OUT / "offline-final/verification.json")
    recorded_ann = read(OUT / "live-capacity/effective_prompts.json")["prompts"]
    recorded_gate = read(current / "effective_prompts.json")["prompts"]
    inventory = read(ORIGINAL / "attachment_selection.json")["files"]
    checks = {"protected_implementations": all(sha(ROOT / p) == digest for p, digest in baseline["protected"].items()),
        "back_prompts": all(__import__('hashlib').sha256(load_prompt(p).encode()).hexdigest() == digest for p, digest in baseline["back_prompts"].items()),
        "final_replay_source": all(sha(ROOT / p) == digest for p, digest in replay["final_source_sha256"].items()),
        "unchanged_live_announcement_prompts": all(load_prompt(p) == text for p, text in recorded_ann.items() if p != "screen-page-v6"),
        "unchanged_live_gate_prompt": load_prompt("screen-page-v7") == recorded_gate["screen-page-v7"],
        "all_50_final_public_results_equal": replay["all_equal"],
        "all_recorded_requests_consumed": all(row["unused_requests"] == 0 for row in replay["cases"]),
        "html": all(sha(manifest["html"][aid]) == digest for aid, digest in manifest["html_sha256"].items()),
        "sampled_attachments": all(sha(row["path"]) == row["digest"] for row in inventory),
        "gold_workbook": sha(gold["source"]) == gold["sha256"]}
    source_changes = [p for p, digest in manifest["source_sha256"].items() if sha(ROOT / p) != digest]
    write(OUT / "integrity.json", {"checks": checks, "source_changes_after_live": source_changes,
          "explanation": "Final changes propagate attachment reception failures to existing unresolved records and correct audit selection. All 50 live request/response traces replayed by exact content address; public results are identical. Announcement and gate API artifacts retain separate source snapshots."})
    assert all(checks.values())
    print(json.dumps({key: {name: value[name] for name in ("outputs", "announcement_status", "alignment", "literal_HTML_name_diagnostic", "front_calls")}
                      for key, value in result.items()}, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
