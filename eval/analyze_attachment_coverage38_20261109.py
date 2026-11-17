"""Offline analysis of the paused 38-case attachment experiment. Network forbidden."""
from __future__ import annotations

from collections import Counter
import copy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "eval")]
import expand_attachment_coverage_20261109 as experiment
from evaluate_front_half_20261108 import read, write, sha, forbid_ocr
from ict.config import MAX_PAGES_PER_FILE, MAX_PAGES_PER_ANNOUNCEMENT

SOURCE = ROOT / "work/attachment-coverage-expanded-20261109"
OUT = SOURCE / "analysis38"


def load():
    manifest = read(SOURCE / "manifest.json")
    prepared = [read(p) for p in (SOURCE / "prepared").glob("*.json")]
    by_file = {(p["source"]["announcement_id"], p["source"]["file_id"]): p for p in prepared}
    ids = sorted({aid for aid, _ in by_file})
    files = [f for f in manifest["files"] if f["announcement_id"] in ids]
    assert len(ids) == 38 and len(files) == len(prepared) == 314
    assert all((f["announcement_id"], f["file_id"]) in by_file for f in files)
    pages = [p for f in files for p in by_file[(f["announcement_id"], f["file_id"])]["pages"]]
    assert len(pages) == 1840
    frozen = dict(manifest, files=files, contexts={aid: manifest["contexts"][aid] for aid in ids})
    write(OUT / "manifest.json", frozen)
    write(OUT / "page_manifest.json", {"pages": pages,
          "preparation_failures": [e for p in prepared for e in p["failures"]]})
    return frozen, prepared, pages


def metric(rows, key):
    targets = [r for r in rows if r.get(key) and r[key].get("target") is True]
    def covered(r):
        return r["entered"] and (r["action"] == "ocr" or r[key].get("reading") == "native")
    return {"labeled": sum(r.get(key) is not None for r in rows), "targets": len(targets),
            "target_cases": len({r["announcement_id"] for r in targets}),
            "target_reading": dict(Counter(r[key].get("reading") for r in targets)),
            "covered": sum(covered(r) for r in targets),
            "covered_by_route": dict(Counter(r["action"] for r in targets if covered(r))),
            "missed_by_cause": dict(Counter(r["loss"] if not r["entered"] else "wrong_reading" for r in targets if not covered(r))),
            "uncertain": sum(r.get(key) is not None and r[key].get("target") == "uncertain" for r in rows),
            "selected_negative": sum(r["entered"] and r.get(key) is not None and r[key].get("target") is False for r in rows)}


def analyze(manifest, prepared, pages):
    rows = []
    job_selected, pages_file, pages_ann, contexts_file, contexts_ann = {}, Counter(), Counter(), Counter(), Counter()
    for p in pages:
        key, file_key, aid = p["page_key"], p["file_key"], p["announcement_id"]
        gate_path = SOURCE / "gates" / (key + ".json")
        if gate_path.exists():
            g = read(gate_path)
            origin = "saved"
        elif p["hard_ignored"]:
            g, origin = {"action": "skip", "method": "rule_name_ignore"}, "offline_structural"
        elif not p["in_read_budget"]:
            g, origin = {"action": "uncovered", "method": "parse_page_budget"}, "offline_structural"
        elif not p["gate_images"]:
            g, origin = {"action": "extract", "method": "native_direct"}, "offline_structural"
        else:
            g, origin = {"action": "failed", "method": "model_page", "error": "not_executed_before_pause"}, "not_executed"
        write(OUT / "gates" / (key + ".json"), g)
        row = {k: p[k] for k in ("page_key", "file_key", "announcement_id", "file_name", "stratum", "page_no", "unit_no", "physical_page")}
        row.update(action=g["action"], method=g["method"], gate_origin=origin, gate_reason=g.get("reason"),
                   gate_error=g.get("error"), in_read_budget=p["in_read_budget"], hard_ignored=p["hard_ignored"],
                   has_image=bool(p["gate_images"]), entered=False, loss=None, selected_contexts=0)
        if g["action"] in ("ocr", "extract"):
            job = (file_key, p["page_no"], p["coordinate_kind"])
            if job not in job_selected:
                job_selected[job] = pages_file[file_key] < MAX_PAGES_PER_FILE and pages_ann[aid] < MAX_PAGES_PER_ANNOUNCEMENT
                if job_selected[job]:
                    pages_file[file_key] += 1
                    pages_ann[aid] += 1
            if not job_selected[job]:
                row["loss"] = "extraction_page_budget"
            else:
                room = min(MAX_PAGES_PER_FILE - contexts_file[file_key], MAX_PAGES_PER_ANNOUNCEMENT - contexts_ann[aid])
                count = 1 if g["action"] == "ocr" else p["extraction_context_count"]
                used = min(count, max(0, room))
                row["entered"] = g["action"] == "ocr" or used > 0
                row["selected_contexts"] = used
                if g["action"] == "extract" and not used:
                    row["loss"] = "extraction_context_budget"
                contexts_file[file_key] += used
                contexts_ann[aid] += used
        else:
            row["loss"] = "not_executed" if origin == "not_executed" else g["method"] if g["method"] != "model_page" else g["action"]
        for name, folder in (("raw_reference", "references"), ("audit", "audits")):
            path = SOURCE / folder / (key + ".json")
            record = read(path) if path.exists() else {"error": "not_executed"}
            row[name] = record.get("output") if not record.get("error") else None
            row[name + "_error"] = record.get("error")
        row["best_available"] = row["audit"] if row["audit"] is not None else row["raw_reference"]
        row["label_level"] = "audit" if row["audit"] is not None else "raw_only" if row["raw_reference"] is not None else "unlabeled"
        rows.append(row)
    manual_path = OUT / "source_review.json"
    reviews = read(manual_path)["reviews"] if manual_path.exists() else []
    for review in reviews:
        row = next(r for r in rows if r["page_key"] == review["page_key"])
        row["source_review"] = review
    summary = {"cases": len(manifest["contexts"]), "files": len(manifest["files"]), "units": len(pages),
          "physical_pages": sum(p["physical_page"] for p in pages), "logical_contexts": sum(not p["physical_page"] for p in pages),
          "partitions": dict(Counter("hard_ignore" if p["hard_ignored"] else "outside_parse_budget" if not p["in_read_budget"]
                          else "image_gate" if p["gate_images"] else "native_direct" for p in pages)),
          "gate_origin": dict(Counter(r["gate_origin"] for r in rows)), "actions": dict(Counter(r["action"] for r in rows)),
          "model_actions": dict(Counter(r["action"] for r in rows if r["method"] == "model_page")),
          "entered": dict(Counter(r["action"] for r in rows if r["entered"])),
          "entry_cases": len({r["announcement_id"] for r in rows if r["entered"]}),
          "losses": dict(Counter(r["loss"] for r in rows if not r["entered"])),
          "raw_reference": metric(rows, "raw_reference"), "audited": metric(rows, "audit"),
          "best_available_provisional": metric(rows, "best_available"), "source_reviewed": metric(rows, "source_review"),
          "reference_errors": dict(Counter(r["raw_reference_error"] for r in rows if r["raw_reference_error"])),
          "audit_errors": dict(Counter(r["audit_error"] for r in rows if r["audit_error"])),
          "label_levels": dict(Counter(r["label_level"] for r in rows)),
          "prepared_failures": [e for p in prepared for e in p["failures"]],
          "uninspected_outside_budget": sum(max(0,b["pages_total"]-len(b["within"])-len(b["outside_audit"]))
                      for p in prepared for b in p["budget"]),
          "one_context_per_ocr_page_assumed": True, "real_ocr": False, "new_api_calls": 0}
    ocr = [r for r in rows if r["entered"] and r["action"] == "ocr"]
    summary["selected_ocr_source_review"] = {
        "selected": len(ocr), "reviewed": sum("source_review" in r for r in ocr),
        "useful": sum(r.get("source_review", {}).get("target") is True for r in ocr),
        "overinspection": sum(r.get("source_review", {}).get("target") is False for r in ocr),
        "useful_by_category": dict(Counter(r["source_review"]["category"] for r in ocr
                                           if r.get("source_review", {}).get("target") is True)),
        "not_a_recall_denominator": True}
    summary["by_case"] = {aid: {"units": len(part), "entered": dict(Counter(r["action"] for r in part if r["entered"])),
                  "model_actions": dict(Counter(r["action"] for r in part if r["method"] == "model_page")),
                  "raw": metric(part, "raw_reference"), "audit": metric(part, "audit"),
                  "source_reviewed": metric(part, "source_review")} for aid in manifest["contexts"]
                  for part in [[r for r in rows if r["announcement_id"] == aid]]}
    write(OUT / "rows.json", rows)
    write(OUT / "summary.json", summary)
    write(OUT / "integrity.json", {"production_unchanged": all(sha(ROOT/p)==h for p,h in manifest["source_sha256"].items()),
          "attachments_unchanged": all(sha(f["path"])==f["digest"] for f in manifest["files"]),
          "contexts_unchanged": all(sha(experiment.ANN/"contexts"/(aid+".json"))==h for aid,h in manifest["contexts"].items()),
          "api_calls_this_analysis": 0})
    print(json.dumps({k:v for k,v in summary.items() if k not in ("by_case","reference_errors","audit_errors","prepared_failures")},ensure_ascii=True))


if __name__ == "__main__":
    import httpx
    def no_network(*args, **kwargs):
        raise AssertionError("Offline analysis: network forbidden")
    httpx.Client.send = no_network
    forbid_ocr()
    manifest, prepared, pages = load()
    analyze(manifest, prepared, pages)
    if "--verify-routing" in sys.argv:
        experiment.OUT = OUT
        experiment.verify_routing(manifest)
