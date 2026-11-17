"""Live API evaluation of the frozen front half. Never calls the OCR service or database."""
from __future__ import annotations
import argparse
import base64
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import ExitStack
from dataclasses import asdict
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "one_shot_baseline")]
from ict.config import llm_settings, concurrency_settings
from ict.llm import LLMClient, LLMResult, OpenAICompatibleClient
from ict.pipeline import run_announcement
from ict.parse.census import build_census
from ict.parse.screen import NAME_IGNORE

GOLD_DIR = ROOT / "work/e2e-20261017-replays/revised-evaluation"
OUT = ROOT / "work/evaluation-front-half-20261108"
PROTECTED = [p for p in (ROOT / "src/ict").rglob("*.py")] + [p for p in (ROOT / "src/ict/prompts").rglob("*.md")]


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def settings(source):
    if source == "production":
        return llm_settings()
    from build_request import model_settings
    return model_settings()


class Recorded(LLMClient):
    def __init__(self, config, folder):
        self.inner = OpenAICompatibleClient(config["base_url"], config["api_key"], config["model"],
            timeout=float(config["timeout"]), connect_timeout=float(config.get("connect_timeout", 15)))
        self.folder, self.lock, self.seq = Path(folder), threading.Lock(), 0
        self.model = config["model"]

    def call(self, kind, step, version, user, images=None, thinking=False):
        with self.lock:
            self.seq += 1
            number = self.seq
        path = self.folder / ("%03d_%s.json" % (number, step))
        record = {"step": step, "prompt_version": version, "kind": kind, "user": user, "thinking": thinking,
                  "image_sha256": [hashlib.sha256(image.encode()).hexdigest() for image in images or []]}
        started = time.perf_counter()
        try:
            result = (self.inner.complete_with_images(step=step, prompt_version=version, images=images, user_text=user)
                      if images is not None else self.inner.complete(step=step, prompt_version=version, user=user, thinking=thinking))
            record["result"] = asdict(result)
            return result
        except Exception as exc:
            record["error"] = str(exc).replace(self.inner.api_key, "[REDACTED]")
            raise
        finally:
            record["elapsed_s"] = time.perf_counter() - started
            write(path, record)

    def complete(self, *, step, prompt_version, user, thinking=False):
        return self.call("text", step, prompt_version, user, thinking=thinking)

    def complete_with_images(self, *, step, prompt_version, images, user_text):
        return self.call("vision", step, prompt_version, user_text, images=images)


def forbid_ocr():
    from ict.parse.client import ParseClient
    def forbidden(*args, **kwargs):
        raise AssertionError("This experiment must not call the OCR server")
    ParseClient.parse = forbidden


def freeze():
    path = OUT / "manifest.json"
    if path.exists():
        manifest = read(path)
        assert all(sha(ROOT / name) == digest for name, digest in manifest["source_sha256"].items()), "Production source changed during evaluation"
        return manifest
    gold = read(GOLD_DIR / "gold_snapshot.json")
    html = {}
    for aid in gold["ids"]:
        old = ROOT / "one_shot_baseline/runs" / aid / "manifest.json"
        path_hint = Path(read(old)["html"]) if old.exists() else None
        if path_hint and path_hint.is_file():
            html[aid] = str(path_hint)
        else:
            matches = sorted((ROOT / "data").rglob(aid + ".html"))
            assert matches, aid
            html[aid] = str(matches[0])
    manifest = {"date": "2026-11-08", "ids": gold["ids"], "html": html,
        "source_sha256": {str(path.relative_to(ROOT)): sha(path) for path in PROTECTED},
        "html_sha256": {aid: sha(path) for aid, path in html.items()}, "gold_sha256": sha(GOLD_DIR / "gold_snapshot.json"),
        "design": {"announcement_cases": 50, "attachment_sampling_seed": "20261108",
                   "attachment_sample": "Structural file strata; fixed before live decisions. Full budget window plus five sampled outside-budget pages for long files.",
                   "no_ocr": True, "no_database": True, "no_rule_or_prompt_changes": True,
                   "availability_reference": "Blinded API reference using original HTML and frozen gold targets; not human gold.",
                   "page_reference": "Blinded higher-resolution page reference with native text, original announcement, known observations and actual needs; not human gold."}}
    write(OUT / "manifest.json", manifest)
    return manifest


def announcement(aid, manifest, config):
    report = OUT / "announcement_runs" / aid / "10_run_report.json"
    if report.exists():
        return {"announcement_id": aid, "cached": True, "status": read(report)["status"]}
    path = Path(manifest["html"][aid])
    client = Recorded(config, OUT / "calls/announcements" / aid)
    started = time.perf_counter()
    try:
        result = run_announcement(aid, llm=client, html_dir=path.parent,
            attachments_root=OUT / "empty_attachments", runs_root=OUT / "announcement_runs")
        row = {"announcement_id": aid, "status": result.status, "elapsed_s": time.perf_counter() - started,
               "counts": result.counts, "llm_calls": result.llm_calls}
    except Exception as exc:
        row = {"announcement_id": aid, "exception": type(exc).__name__,
               "error": str(exc).replace(config["api_key"], "[REDACTED]"), "elapsed_s": time.perf_counter() - started}
    write(OUT / "announcement_cases" / (aid + ".json"), row)
    return row


def announcements(manifest, config, limit=None):
    forbid_ocr()
    ids = manifest["ids"][:limit] if limit else manifest["ids"]
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency_settings()["announcement_workers"]) as pool:
        futures = {pool.submit(announcement, aid, manifest, config): aid for aid in ids}
        for future in as_completed(futures):
            row = future.result()
            print(json.dumps(row, ensure_ascii=True), flush=True)
    write(OUT / "announcement_batch.json", {"cases": len(ids), "wall_s": time.perf_counter() - started,
                                           "credential_source": ARGS.credentials, "model": config["model"]})


def inventory(manifest):
    import pymupdf
    from ict.parse.peek import docx_media
    pymupdf.TOOLS.mupdf_display_errors(False)
    pymupdf.TOOLS.mupdf_display_warnings(False)
    entries, summary = [], Counter()
    for aid in manifest["ids"]:
        directory = ROOT / "work/attachments" / aid
        if not directory.is_dir():
            continue
        files, duplicates = build_census(directory)
        for entry in files:
            row = {"announcement_id": aid, "file_id": entry.file_id, "file_name": entry.name,
                   "path": str(entry.path), "format": entry.fmt, "pages": entry.pages, "digest": entry.digest,
                   "hard_ignored": bool(NAME_IGNORE.search(entry.name))}
            try:
                if row["hard_ignored"]:
                    row["stratum"] = "hard_ignore"
                elif entry.fmt == "pdf":
                    with pymupdf.open(entry.path) as document:
                        sample = [document[number] for number in sorted({0, len(document) // 2, len(document) - 1})]
                        image = any(page.get_images() for page in sample)
                        text = any(page.get_text().strip() for page in sample)
                        row["stratum"] = "long_pdf" if len(document) > 40 else "scan_pdf" if not text else "mixed_pdf" if image else "native_pdf"
                elif entry.fmt == "docx":
                    row["stratum"] = "docx_images" if docx_media(entry.path) else "docx_native"
                else:
                    row["stratum"] = entry.fmt
            except Exception as exc:
                row["stratum"], row["error"] = "unreadable", str(exc)
            row["reader_warnings"] = pymupdf.TOOLS.mupdf_warnings()
            entries.append(row)
            summary[row["stratum"]] += 1
    write(OUT / "attachment_inventory.json", {"files": entries, "strata": dict(summary)})
    selected, used, cases = [], set(), Counter()
    quotas = {"scan_pdf": 3, "mixed_pdf": 3, "native_pdf": 3, "long_pdf": 3,
              "docx_native": 2, "docx_images": 2, "ole": 2, "hard_ignore": 2}
    for stratum, quota in quotas.items():
        rows = sorted((row for row in entries if row["stratum"] == stratum),
                      key=lambda row: hashlib.sha256(("20261108" + row["digest"] + row["announcement_id"]).encode()).hexdigest())
        added = 0
        for row in rows:
            if row["digest"] in used or cases[row["announcement_id"]] >= 2:
                continue
            selected.append(row)
            cases[row["announcement_id"]] += 1
            used.add(row["digest"])
            added += 1
            if added == quota:
                break
    write(OUT / "attachment_selection.json", {"quotas": quotas, "seed": "20261108", "files": selected})
    print(json.dumps({"strata": dict(summary), "selected_files": len(selected), "selected_cases": len(cases)}), flush=True)


def load_score():
    spec = importlib.util.spec_from_file_location("frozen_front_half_score", GOLD_DIR / "score.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def score_announcements():
    from ict.official import HEADERS
    scorer, gold = load_score(), read(GOLD_DIR / "gold_snapshot.json")
    results, sheets, records = scorer.load_results(OUT / "announcement_runs", gold["ids"])
    cells, alignment = [], {}
    for name, definition in gold["sheets"].items():
        row, alignment[name], _ = scorer.cells_for(name, definition["rows"], sheets[name], definition["blue_headers"], [])
        cells.extend(row)
    metrics = {"all_gold_unadjusted": scorer.metrics(cells),
        "by_sheet_unadjusted": {name: scorer.metrics([cell for cell in cells if cell["sheet"] == name]) for name in gold["sheets"]},
        "by_field_unadjusted": {name + " / " + field: scorer.metrics([cell for cell in cells if cell["sheet"] == name and cell["field"] == field])
                               for name, definition in gold["sheets"].items() for field in definition["blue_headers"]},
        "alignment": alignment, "records": records,
        "warning": "Announcement-only outputs compared with full-case gold, which includes attachment-only information. These unadjusted numbers are not announcement extraction accuracy."}
    write(OUT / "announcement_unadjusted_metrics.json", metrics)
    write(OUT / "announcement_cells.json", cells)
    write(OUT / "announcement_results.json", results)
    print(json.dumps({"outputs": len(results), "rows": {name: len(rows) for name, rows in sheets.items()}}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", required=True, choices=["prepare", "smoke", "announcements", "score", "pages", "gate-smoke", "gates", "reference-smoke", "references"])
    parser.add_argument("--credentials", choices=["production", "one-shot"], default="production")
    ARGS = parser.parse_args()
    manifest = freeze()
    if ARGS.phase == "prepare":
        inventory(manifest)
    elif ARGS.phase in ("smoke", "announcements"):
        announcements(manifest, settings(ARGS.credentials), 1 if ARGS.phase == "smoke" else None)
    elif ARGS.phase == "score":
        score_announcements()
    else:
        from front_half_reference_20261108 import prepare_pages, page_gates, references
        if ARGS.phase == "pages":
            prepare_pages(manifest)
        elif ARGS.phase in ("gate-smoke", "gates"):
            page_gates(settings(ARGS.credentials), 1 if ARGS.phase == "gate-smoke" else None)
        elif ARGS.phase in ("reference-smoke", "references"):
            references(settings(ARGS.credentials), 1 if ARGS.phase == "reference-smoke" else None)
