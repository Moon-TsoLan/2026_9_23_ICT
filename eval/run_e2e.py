"""Run announcements through the pipeline and score them against the gold files.

    python eval/run_e2e.py t20260905_27275031 t20260401_26346106
"""

from __future__ import annotations

import json
import re
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ict.index.build import build_index
from ict.pipeline import run_batch

REPO = Path(__file__).resolve().parents[1]
PUNCT = re.compile(r"[（）()\[\]【】\s，,。．.、;；:：/\\\-—_]")


def norm(value) -> str:
    return PUNCT.sub("", str(value or "")).lower()


def score(announcement_id: str, run_dir: Path) -> dict:
    gold_path = REPO / "eval" / "gold" / ("%s.json" % announcement_id)
    projects_path = run_dir / "projects.json"
    if not gold_path.exists() or not projects_path.exists():
        return {"error": "missing gold or projects"}
    gold = json.loads(gold_path.read_text(encoding="utf-8"))["projects"]
    got = json.loads(projects_path.read_text(encoding="utf-8"))
    by_package = {str(project.get("package_no")): project for project in got}
    rows = []
    total_gold = total_hit = 0
    for want in gold:
        package = str(want.get("package_no"))
        have = by_package.get(package) or {}
        want_names = [norm(cob.get("object_name")) for cob in want.get("cobs") or [] if cob.get("object_name")]
        have_names = [norm(cob.get("object_name")) for cob in have.get("cobs") or [] if cob.get("object_name")]
        hits = sum(1 for name in want_names if any(name == other or name in other or other in name
                                                   for other in have_names))
        want_winners = [norm(sub.get("supplier_name")) for sub in want.get("subs") or [] if sub.get("is_winner")]
        have_winners = [norm(sub.get("supplier_name")) for sub in have.get("subs") or [] if sub.get("is_winner")]
        winner_hits = sum(1 for name in want_winners if any(name and (name in other or other in name)
                                                            for other in have_winners))
        total_gold += len(want_names)
        total_hit += hits
        rows.append({"package": package, "gold_cobs": len(want_names), "got_cobs": len(have_names), "name_hits": hits,
                     "gold_winners": len(want_winners), "winner_hits": winner_hits,
                     "amount_got": have.get("package_total_amount"), "amount_gold": want.get("package_total_amount")})
    return {"packages": rows, "cob_recall": "%d/%d" % (total_hit, total_gold)}


def main() -> None:
    argv = sys.argv[1:]
    workers = None
    if "--workers" in argv:
        position = argv.index("--workers")
        workers = int(argv[position + 1])
        del argv[position:position + 2]
    ids = argv or ["t20260905_27275031"]
    for announcement_id in ids:
        try:
            build_index(announcement_id)
        except Exception:
            traceback.print_exc()
    started = time.perf_counter()
    reports = run_batch(ids, workers=workers)
    batch_wall = time.perf_counter() - started
    for announcement_id in ids:
        print("=" * 90, flush=True)
        print(announcement_id, flush=True)
        report = reports.get(announcement_id)
        if report is None:
            print("no report", flush=True)
            continue
        print("status=%s wall=%.1fs counts=%s" % (report.status, report.duration_ms / 1000.0,
                                                  report.counts), flush=True)
        print("llm_calls=%s" % (report.llm_calls,), flush=True)
        print("failures=%s" % (report.failure_summary,), flush=True)
        run_dir = REPO / "work" / "runs" / announcement_id
        step5 = run_dir / "05_file_decisions.json"
        if step5.exists():
            decisions = json.loads(step5.read_text(encoding="utf-8")).get("file_decisions") or []
            kept = [item for item in decisions if item.get("read_strategy") == "target_pages"]
            print("screen kept=%d/%d" % (len(kept), len(decisions)), flush=True)
            for item in kept:
                print("   keep", item["file_id"], item["file_class"], (item.get("reason") or "")[:56], flush=True)
        step5b = run_dir / "05b_parsed_pages.json"
        if step5b.exists():
            parsed = json.loads(step5b.read_text(encoding="utf-8"))
            for entry in parsed.get("files") or []:
                print("   parsed", entry["file_id"], entry["name"][:34], "pages=%d chars=%d tables=%d" % (
                    entry["pages"], entry["chars"], entry["tables"]), flush=True)
        step7 = run_dir / "07_attachment_extraction.json"
        if step7.exists():
            produced = json.loads(step7.read_text(encoding="utf-8"))
            names = [cand["fields"].get("object_name", {}).get("raw_value") for cand in produced.get("candidates") or []
                     if cand["entity_type"] == "cob"]
            pages = sorted({(cand.get("source") or {}).get("page_no") for cand in produced.get("candidates") or []})
            print("attachment cobs=%d pages_used=%s" % (len([n for n in names if n]), pages), flush=True)
        print("score:", json.dumps(score(announcement_id, run_dir), ensure_ascii=False), flush=True)
    print("batch wall=%.1fs (workers=%s)" % (batch_wall, workers or "default"), flush=True)


if __name__ == "__main__":
    main()
