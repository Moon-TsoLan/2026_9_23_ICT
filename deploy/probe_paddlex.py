"""Probe the PaddleOCR-VL pipeline on the server and report the real result shape.

Answers the three things that cannot be checked from Windows: which fields a per-page
result exposes, how long a page takes when layout runs on this box while recognition goes
to vLLM, and whether restructure_pages actually stitches a table that spans pages.

    python3 probe_paddlex.py --file '???????????([2]).pdf' --pages 1-6 --out probe_report.json
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path


def env(key: str, default: str = "") -> str:
    return os.environ.get(key, default) or default


def head(value, limit: int = 300) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text[:limit]


def describe(result) -> dict:
    info: dict = {"type": type(result).__name__}
    info["attrs"] = [a for a in dir(result) if not a.startswith("__")][:60]
    view: dict = {}
    try:
        raw = result.json
        if callable(raw):
            raw = raw()
        res = raw["res"] if isinstance(raw, dict) and "res" in raw else raw
        view["res_keys"] = sorted(res.keys())[:40]
        blocks = res.get("parsing_res_list") if isinstance(res, dict) else getattr(res, "parsing_res_list", [])
        labels: dict[str, int] = {}
        sample = []
        for block in blocks or []:
            if isinstance(block, dict):
                label = str(block.get("block_label"))
                content = block.get("block_content")
                bbox = block.get("block_bbox")
                keys = sorted(block.keys())
            else:
                label = str(getattr(block, "label", "?"))
                content = str(getattr(block, "content", ""))
                bbox = getattr(block, "bbox", None)
                keys = []
            labels[label] = labels.get(label, 0) + 1
            if len(sample) < 5:
                sample.append({"label": label, "bbox": bbox, "keys": keys, "content": head(content)})
        view["block_count"] = len(blocks or [])
        view["labels"] = labels
        view["first_blocks"] = sample
    except Exception as exc:  # noqa: BLE001 - this is a probe, record everything
        view["error"] = f"{type(exc).__name__}: {exc}"
    info["json_view"] = view
    for name in ("_to_markdown", "to_markdown"):
        method = getattr(result, name, None)
        if not callable(method):
            continue
        try:
            produced = method(pretty=False, show_formula_number=False)
        except TypeError:
            try:
                produced = method()
            except Exception as exc:  # noqa: BLE001
                info[f"{name}_error"] = f"{type(exc).__name__}: {exc}"
                continue
        except Exception as exc:  # noqa: BLE001
            info[f"{name}_error"] = f"{type(exc).__name__}: {exc}"
            continue
        if isinstance(produced, dict):
            info["markdown_keys"] = sorted(produced.keys())
            info["markdown_head"] = head(produced.get("markdown_texts") or produced.get("markdown"))
        else:
            info["markdown_head"] = head(produced)
        break
    return info


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True)
    parser.add_argument("--out", default="probe_report.json")
    parser.add_argument("--device", default=env("DEVICE", "cpu"))
    parser.add_argument("--pages", default="", help="for example 1-6; empty means the whole file")
    parser.add_argument("--merge", default="1", help="1 to also exercise restructure_pages")
    args = parser.parse_args()

    import paddle
    import paddleocr

    report: dict = {
        "paddle": paddle.__version__,
        "paddleocr": getattr(paddleocr, "__version__", "?"),
        "device": args.device,
        "vl_server_url": env("ICT_VL_SERVER_URL", "native (recognition local)"),
        "file": str(Path(args.file).resolve()),
    }
    print(json.dumps({k: report[k] for k in ("paddle", "paddleocr", "device", "vl_server_url")}, ensure_ascii=False), flush=True)

    from paddleocr import PaddleOCRVL

    kwargs = {"pipeline_version": "v1.6", "device": args.device, "enable_mkldnn": False,
              "use_ocr_for_image_block": True}
    if env("ICT_VL_SERVER_URL"):
        kwargs.update({
            "vl_rec_backend": "vllm-server",
            "vl_rec_server_url": env("ICT_VL_SERVER_URL"),
            "vl_rec_api_model_name": env("ICT_VL_API_MODEL_NAME", "PaddlePaddle/PaddleOCR-VL-1.6"),
            "vl_rec_max_concurrency": int(env("ICT_PARSE_MAX_CONCURRENCY", "16")),
        })
    started = time.perf_counter()
    pipeline = PaddleOCRVL(**kwargs)
    report["init_seconds"] = round(time.perf_counter() - started, 1)
    print("pipeline ready in", report["init_seconds"], "s", flush=True)

    target = args.file
    if args.pages:
        import pypdfium2 as pdfium

        low, high = (int(part) for part in args.pages.split("-"))
        source = pdfium.PdfDocument(args.file)
        cut = pdfium.PdfDocument.new()
        cut.import_pages(source, pages=list(range(low - 1, min(high, len(source)))))
        target = "/tmp/probe_cut.pdf"
        cut.save(target)
        cut.close()
        source.close()
        report["sliced"] = f"{low}-{high} -> {target}"
        print("sliced", report["sliced"], flush=True)

    pages = []
    each = time.perf_counter()
    for index, page in enumerate(pipeline.predict_iter(target, format_block_content=True), start=1):
        info = describe(page)
        info["page_seq"] = index
        info["seconds"] = round(time.perf_counter() - each, 2)
        each = time.perf_counter()
        pages.append(info)
        print(f"page {index}: {info['json_view'].get('block_count')} blocks "
              f"{info['json_view'].get('labels')} {info['seconds']}s", flush=True)
    report["page_count"] = len(pages)
    report["pages"] = pages[:12]

    if args.merge == "1" and len(pages) >= 2:
        try:
            raw_pages = list(pipeline.predict_iter(target, format_block_content=True))
            merged = list(pipeline.restructure_pages(
                raw_pages, merge_tables=True, relevel_titles=True, concatenate_pages=False))
            after = [describe(item)["json_view"].get("labels") for item in merged]
            report["restructure"] = {"input_pages": len(raw_pages), "output_pages": len(merged), "labels_after": after[:8]}
        except Exception as exc:  # noqa: BLE001
            report["restructure_error"] = f"{type(exc).__name__}: {exc}"

    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("saved", args.out, flush=True)


if __name__ == "__main__":
    main()
