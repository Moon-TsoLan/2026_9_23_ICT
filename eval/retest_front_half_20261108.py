"""Run the approved repaired-front-half experiment. No OCR, DB, or old run writes."""
from __future__ import annotations
import argparse
import base64
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "eval")]
from evaluate_front_half_20261108 import Recorded, read, write, sha, settings, forbid_ocr, load_score, OUT as OLD
from ict.concurrency import CallCounter
from ict.llm import load_prompt
from ict.packages import package_context
import ict.pipeline as pipeline
from ict.parse.screen import inspect_page
from ict.schemas import CandidateFile

OUT = ROOT / "work/front-half-repair-20261108/live"
SMOKE = ["t20260202_26139731", "t20260202_26140146", "t20260206_26157725", "t20260205_26151548", "t20260212_26175071"]
original_process = pipeline.process_attachment_pages


def capture_context(*args, **kwargs):
    run_id, project_name, plans = args[:3]
    write(OUT / "contexts" / (run_id.removeprefix("run_") + ".json"), {
        "run_id": run_id, "project_name": project_name,
        "package_registry": [package_context(row) for row in plans.projects],
        "needs": [need for row in plans.projects for need in row.needs],
        "known_observations": kwargs["known_observations"]})
    return original_process(*args, **kwargs)


def freeze():
    baseline = read(ROOT / "work/front-half-repair-20261108/baseline.json")
    assert all(sha(ROOT / p) == digest for p, digest in baseline["protected"].items())
    assert all(hashlib.sha256(load_prompt(p).encode()).hexdigest() == digest for p, digest in baseline["back_prompts"].items())
    old = read(OLD / "manifest.json")
    sources = {str(p.relative_to(ROOT)): sha(p) for p in list((ROOT / "src/ict").rglob("*.py")) + list((ROOT / "src/ict/prompts").rglob("*.md"))}
    current = {"ids": old["ids"], "html": old["html"], "html_sha256": old["html_sha256"],
               "source_sha256": sources, "no_ocr": True, "no_database": True,
               "page_contexts": ["frozen_historical_HTML", "current_announcement_output"]}
    path = OUT / "manifest.json"
    if path.exists():
        assert read(path) == current, "Do not mix code revisions in the frozen live run"
    else:
        write(path, current)
    assert all(sha(old["html"][aid]) == digest for aid, digest in old["html_sha256"].items())
    for name in sources:
        target = OUT / "source_snapshot" / name
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((ROOT / name).read_bytes())
    from ict.fields import STEP_FIELDS, PROMPT_OUTPUT_LIMITS
    prompt_path = OUT / "effective_prompts.json"
    if not prompt_path.exists():
        write(prompt_path, {"prompts": {version: load_prompt(version) for version in STEP_FIELDS},
                            "output_limits": PROMPT_OUTPUT_LIMITS})
    return current


def announcements(manifest, config, smoke=False):
    pipeline.process_attachment_pages = capture_context
    ids = SMOKE if smoke else manifest["ids"]
    started = time.perf_counter()
    def run(aid):
        path = OUT / "runs" / aid / "10_run_report.json"
        if path.exists():
            return {"announcement_id": aid, "cached": True, "status": read(path)["status"]}
        source = Path(manifest["html"][aid])
        result = pipeline.run_announcement(aid, llm=Recorded(config, OUT / "calls/announcements" / aid),
            html_dir=source.parent, attachments_root=OUT / "empty_attachments", runs_root=OUT / "runs")
        understanding = read(path.parent / "01_announcement_understanding.json")
        return {"announcement_id": aid, "status": result.status, "body_status": understanding["status"],
                "packages": len(understanding["packages"]), "html_candidates": result.counts["html_candidates"],
                "calls": result.llm_calls, "duration_ms": result.duration_ms}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(run, aid) for aid in ids]
        for future in as_completed(futures):
            print(json.dumps(future.result(), ensure_ascii=True), flush=True)
    write(OUT / ("smoke_batch.json" if smoke else "announcement_batch.json"),
          {"ids": ids, "wall_s": time.perf_counter() - started, "model": config["model"]})


def historical_context(aid):
    context = read(OLD / "reference_contexts" / (aid + ".json"))
    path = next(Path(p) for p in context["source_sha256"] if p.endswith("08_normalized_candidates.json"))
    observations = CandidateFile.model_validate(read(path)).candidates
    context["known_observations"] = [{"candidate_id": row.candidate_id, "entity_type": row.entity_type,
        "package_no": row.package_no, "fields": {key: {"raw_value": obs.raw_value, "status": obs.status}
            for key, obs in row.fields.items() if obs.raw_value is not None or obs.status != "missing"},
        "source_refs": row.evidence.source_refs if row.evidence else []}
        for row in observations if row.source.source_type == "html"]
    return context


def gates(config, current=False, announcement_dir=None):
    label = "current" if current else "historical"
    pages = [row for row in read(OLD / "page_manifest.json")["pages"]
             if row["in_read_budget"] and not row["hard_ignored"] and row["gate_images"]]
    def run(page):
        path = OUT / ("page_gates_" + label) / (page["page_key"] + ".json")
        if path.exists():
            return
        aid = page["announcement_id"]
        context_path = (announcement_dir or OUT) / "contexts" / (aid + ".json")
        if current and not context_path.exists():
            write(path, {"page_key": page["page_key"], "action": "unprocessed", "reason": "current_announcement_failed"})
            return
        context = read(context_path) if current else historical_context(aid)
        write(OUT / ("gate_contexts_" + label) / (aid + ".json"), context)
        payload = {key: page[key] for key in ("file_id", "file_name", "page_no", "source_segments")}
        payload["package_registry"] = context["package_registry"]
        payload["known_observations"] = context["known_observations"]
        payload["images"] = ["data:image/png;base64," + base64.b64encode(Path(p).read_bytes()).decode() for p in page["gate_images"]]
        try:
            answer, audit = inspect_page(Recorded(config, OUT / ("calls/gates_" + label) / page["page_key"]),
                                         payload, context["needs"], CallCounter())
            result = {"page_key": page["page_key"], **answer, "audit": audit}
        except Exception as exc:
            result = {"page_key": page["page_key"], "action": "failed", "error": str(exc).replace(config["api_key"], "[REDACTED]")}
        write(path, result)
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(run, page) for page in pages]
        for number, future in enumerate(as_completed(futures), 1):
            future.result()
            if number % 10 == 0 or number == len(futures):
                print(json.dumps({"context": label, "completed": number, "pages": len(futures)}), flush=True)


def summarize(manifest, announcement_dir=None):
    announcement_dir = announcement_dir or OUT
    summaries = []
    for aid in manifest["ids"]:
        folder = announcement_dir / "runs" / aid
        if not (folder / "10_run_report.json").exists():
            continue
        a, report = read(folder / "01_announcement_understanding.json"), read(folder / "10_run_report.json")
        summaries.append({"announcement_id": aid, "status": report["status"], "announcement_status": a["status"],
            "packages": len(a["packages"]), "output": (folder / "projects.json").exists(), "counts": report["counts"],
            "failures": report["failure_summary"], "calls": report["llm_calls"], "duration_ms": report["duration_ms"],
            "branch_issues": {branch: a["branch_audit"][branch].get("attempts", []) for branch in ("summary", "body")}})
    scorer = load_score()
    gold = read(OLD.parent / "e2e-20261017-replays/revised-evaluation/gold_snapshot.json")
    results, sheets, _ = scorer.load_results(announcement_dir / "runs", manifest["ids"])
    cells, alignment = [], {}
    for name, definition in gold["sheets"].items():
        part, align, _ = scorer.cells_for(name, definition["rows"], sheets[name], definition["blue_headers"], [])
        cells += part
        alignment[name] = {key: value for key, value in align.items() if not key.startswith("unmatched")}
    gate_rows = []
    for page in read(OLD / "page_manifest.json")["pages"]:
        key = page["page_key"]
        h, c = OUT / "page_gates_historical" / (key + ".json"), OUT / "page_gates_current" / (key + ".json")
        if not h.exists():
            continue
        gate_rows.append({"page_key": key, "announcement_id": page["announcement_id"], "file_name": page["file_name"],
            "page_no": page["page_no"], "old": read(OLD / "page_gates" / (key + ".json")),
            "historical": read(h), "current": read(c) if c.exists() else None,
            "old_reference_unverified": read(OLD / "page_references" / (key + ".json"))["output"]})
    usage = {}
    for group, pattern in (("announcements", "calls/announcements/*/*.json"),
                           ("gates_historical", "calls/gates_historical/*/*.json"), ("gates_current", "calls/gates_current/*/*.json")):
        counts = Counter()
        for path in (announcement_dir if group == "announcements" else OUT).glob(pattern):
            record = read(path)
            counts["calls"] += 1
            for key, value in record.get("result", {}).get("usage", {}).items():
                if isinstance(value, (float, int)):
                    counts[key] += value
        usage[group] = dict(counts)
    summary = {"announcement_directory": str(announcement_dir), "cases": summaries, "outputs": len(results), "row_alignment_full_gold_unadjusted": alignment,
        "metrics_full_gold_unadjusted": {name: scorer.metrics([cell for cell in cells if cell["sheet"] == name]) for name in gold["sheets"]},
        "gate_actions": {group: dict(Counter(row[group]["action"] for row in gate_rows if row[group])) for group in ("old", "historical", "current")},
        "usage": usage, "warnings": ["Full gold includes attachment-only information; these scores are not announcement-only accuracy.",
         "Old page reference is unverified and uses a different information-increment scope; it is not a precision/recall gold standard.",
         "Historical and current page contexts are separate experiments. No OCR, attachment extraction or database calls."]}
    write(OUT / "summary.json", summary)
    write(OUT / "cells_unadjusted.json", cells)
    write(OUT / "gate_comparison.json", gate_rows)
    print(json.dumps({"outputs": len(results), "announcement_status": dict(Counter(row["announcement_status"] for row in summaries)),
                      "gate_actions": summary["gate_actions"], "usage": usage}, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", default="work/front-half-repair-20261108/live-screen")
    parser.add_argument("--announcements-from", default="work/front-half-repair-20261108/live-capacity")
    parser.add_argument("--phase", choices=("smoke", "announcements", "gates-historical", "gates-current", "summary"), required=True)
    args = parser.parse_args()
    OUT = ROOT / args.run_dir
    forbid_ocr()
    frozen = freeze()
    if args.phase == "summary":
        summarize(frozen, ROOT / args.announcements_from)
    elif args.phase in ("smoke", "announcements"):
        announcements(frozen, settings("production"), args.phase == "smoke")
    else:
        gates(settings("production"), args.phase == "gates-current", ROOT / args.announcements_from)
