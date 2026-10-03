"""Warm timing + cross-page table merge verification against one real gold attachment."""
from __future__ import annotations

import json
import os
import time

from paddleocr import PaddleOCRVL

FILE = os.environ.get("PROBE_FILE", "/root/sample_quote.pdf")
URL = os.environ.get("ICT_VL_SERVER_URL", "http://127.0.0.1:6006/v1")
MODEL = os.environ.get("ICT_VL_API_MODEL_NAME", "PaddlePaddle/PaddleOCR-VL-1.6")


def blocks_of(result):
    raw = result.json
    if callable(raw):
        raw = raw()
    res = raw["res"] if isinstance(raw, dict) and "res" in raw else raw
    return res.get("parsing_res_list") or []


def table_stats(result):
    rows_total = 0
    cells_total = 0
    tables = 0
    widths = []
    for block in blocks_of(result):
        if block.get("block_label") != "table":
            continue
        content = block.get("block_content") or ""
        if not content.strip():
            continue
        tables += 1
        rows_total += content.count("<tr")
        cells_total += content.count("<td") + content.count("<th")
        bbox = block.get("block_bbox") or [0, 0, 0, 0]
        widths.append(round(float(bbox[2]) - float(bbox[0]), 1))
    return {"tables": tables, "rows": rows_total, "cells": cells_total, "widths": widths}


def page_view(results):
    out = []
    for index, result in enumerate(results, start=1):
        blocks = blocks_of(result)
        labels = {}
        for block in blocks:
            labels[str(block.get("block_label"))] = labels.get(str(block.get("block_label")), 0) + 1
        out.append({"page": index, "labels": labels, **table_stats(result)})
    return out


def main() -> None:
    report = {"file": FILE, "url": URL, "model": MODEL}
    started = time.perf_counter()
    pipe = PaddleOCRVL(
        pipeline_version="v1.6",
        device="cpu",
        enable_mkldnn=False,
        use_ocr_for_image_block=True,
        vl_rec_backend="vllm-server",
        vl_rec_server_url=URL,
        vl_rec_api_model_name=MODEL,
        vl_rec_max_concurrency=16,
    )
    report["init_seconds"] = round(time.perf_counter() - started, 2)

    passes = []
    for run in range(2):
        t0 = time.perf_counter()
        results = list(pipe.predict_iter(FILE, format_block_content=True))
        wall = time.perf_counter() - t0
        passes.append({
            "run": run + 1,
            "pages": len(results),
            "wall_seconds": round(wall, 2),
            "seconds_per_page": round(wall / max(1, len(results)), 2),
        })
        print(json.dumps(passes[-1]), flush=True)
        if run == 1:
            report["per_page"] = page_view(results)
            merged = list(pipe.restructure_pages(results, merge_tables=True, relevel_titles=True, concatenate_pages=False))
            report["merged"] = page_view(merged)
            big = max((item for item in report["merged"] if item["rows"]), key=lambda item: item["rows"], default=None)
            report["merged_largest_table"] = big
            single = [item["rows"] for item in report["per_page"]]
            report["rows_before_total"] = sum(single)
            report["rows_after_total"] = sum(item["rows"] for item in report["merged"])
    report["passes"] = passes
    with open("/root/probe_merge.json", "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print("saved /root/probe_merge.json", flush=True)


if __name__ == "__main__":
    main()
