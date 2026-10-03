"""Document parsing service: a page-run in, per-page structure out, nothing left on disk.

    set -a; . /opt/ict-parse/parse.env; set +a
    python3 -m uvicorn parse_service:app --host 0.0.0.0 --port 6008 --workers 3

    POST /parse  multipart: file, pages="2-6,9", merge_tables, return_blocks, max_pages
    -> {"ok":true,
        "pages":[{"page_no":3,"markdown":"...","blocks":[{"label","bbox","order","group_id","content"}]}],
        "tables":[{"page_no":3,"index":0,"html":"<table>..."}],
        "meta":{"document_pages":158,"parsed_pages":[3,4,5],"ms":18123,"converted_from":".docx",
                "layout_device":"cpu","warnings":[]}}

Readers are pinned to shapes confirmed on the server by probe_paddlex.py: blocks live in
res["parsing_res_list"] as dicts with block_label / block_bbox / block_content / block_order
/ group_id, and page markdown comes from _to_markdown()["markdown_texts"], where tables are
already HTML and embedded pictures appear as ![](imgs/...).

Throughput unit is the contiguous page-run: pass several ranges and the client can fan out
across worker processes instead of waiting on one long serial file parse.
"""

from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import JSONResponse

OFFICE_SUFFIXES = {".doc", ".docx", ".xls", ".xlsx", ".wps", ".et", ".ppt", ".pptx"}
NATIVE_TABLE_SUFFIXES = {".xls", ".xlsx", ".xlsm", ".csv"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
IMAGE_REF = re.compile(r"!\[[^\]]*\]\([^)]*\)")


def env(key: str, default: str = "") -> str:
    return os.environ.get(key, default) or default


_pipeline = None
_load_lock = threading.Lock()


def get_pipeline():
    global _pipeline
    if _pipeline is None:
        with _load_lock:
            if _pipeline is None:
                from paddleocr import PaddleOCRVL

                kwargs = {
                    "pipeline_version": "v1.6",
                    "device": env("DEVICE", "cpu"),
                    "enable_mkldnn": env("ICT_ENABLE_MKLDNN", "0") == "1",
                    "cpu_threads": int(env("ICT_CPU_THREADS", "4")),
                    "use_ocr_for_image_block": env("ICT_OCR_IMAGE_BLOCKS", "1") == "1",
                }
                if env("ICT_VL_SERVER_URL"):
                    kwargs.update({
                        "vl_rec_backend": "vllm-server",
                        "vl_rec_server_url": env("ICT_VL_SERVER_URL"),
                        "vl_rec_api_model_name": env("ICT_VL_API_MODEL_NAME", "PaddlePaddle/PaddleOCR-VL-1.6"),
                        "vl_rec_max_concurrency": int(env("ICT_PARSE_MAX_CONCURRENCY", "16")),
                    })
                _pipeline = PaddleOCRVL(**kwargs)
    return _pipeline


def soffice_to_pdf(source: Path, workdir: Path) -> Path:
    binary = shutil.which("soffice") or shutil.which("libreoffice")
    if not binary:
        raise RuntimeError("soffice_missing")
    profile = workdir / ("profile-" + uuid.uuid4().hex[:8])
    profile.mkdir(parents=True, exist_ok=True)
    command = [
        binary, "--headless", "--norestore", "--invisible",
        "-env:UserInstallation=file://" + str(profile),
        "--convert-to", "pdf:writer_pdf_Export", "--outdir", str(workdir), str(source),
    ]
    done = subprocess.run(command, capture_output=True, text=True,
                          timeout=int(env("ICT_SOFFICE_TIMEOUT", "180")))
    produced = workdir / (source.stem + ".pdf")
    if not produced.is_file():
        detail = (done.stderr or done.stdout or "")[-300:]
        raise RuntimeError("convert_failed rc=" + str(done.returncode) + " " + detail)
    shutil.rmtree(profile, ignore_errors=True)
    return produced


def parse_range(spec: str, total: int) -> list[int]:
    pages: list[int] = []
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            low, high = part.split("-", 1)
            pages.extend(range(max(1, int(low)), min(int(high), total) + 1))
        else:
            pages.append(int(part))
    return [page for page in dict.fromkeys(pages) if 1 <= page <= total]


def contiguous_runs(pages: list[int]) -> list[list[int]]:
    runs: list[list[int]] = []
    for page in pages:
        if runs and page == runs[-1][-1] + 1:
            runs[-1].append(page)
        else:
            runs.append([page])
    return runs


def slice_pdf(source: Path, wanted: list[int], workdir: Path, tag: str) -> Path:
    """Cut the wanted pages first: predict_iter has no page-range argument."""
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(str(source))
    try:
        kept = [page for page in wanted if 1 <= page <= len(document)]
        if not kept:
            raise RuntimeError("empty_page_range")
        target = pdfium.PdfDocument.new()
        target.import_pages(document, pages=[page - 1 for page in kept])
        out = workdir / ("run-" + tag + ".pdf")
        target.save(str(out))
        target.close()
    finally:
        document.close()
    return out


def res_of(page_res) -> dict:
    raw = page_res.json
    if callable(raw):
        raw = raw()
    return raw["res"] if isinstance(raw, dict) and "res" in raw else raw


def page_blocks(page_res) -> list[dict]:
    out = []
    for block in res_of(page_res).get("parsing_res_list") or []:
        out.append({
            "label": block.get("block_label"),
            "bbox": block.get("block_bbox"),
            "order": block.get("block_order"),
            "group_id": block.get("group_id"),
            "content": block.get("block_content") or "",
        })
    return out


def strip_image_refs_enabled() -> bool:
    return env("ICT_STRIP_IMAGE_REFS", "1") == "1"


def page_markdown(page_res, blocks: list[dict], strip_images: bool) -> str:
    text = ""
    method = getattr(page_res, "_to_markdown", None)
    if callable(method):
        try:
            produced = method(pretty=False, show_formula_number=False)
            if isinstance(produced, dict):
                text = produced.get("markdown_texts") or ""
            else:
                text = str(produced or "")
        except Exception:  # noqa: BLE001 - a page without markdown still has blocks
            text = ""
    if not text.strip():
        text = "\n\n".join(block["content"] for block in blocks if block.get("content"))
    return IMAGE_REF.sub("", text) if strip_images else text


def html_tables(blocks: list[dict]) -> list[str]:
    return [block["content"] for block in blocks
            if block.get("label") == "table" and "<t" in (block.get("content") or "").lower()]


def run_range(source: Path, pages: list[int], workdir: Path, max_pages: int,
              merge_tables: bool, warnings: list[str],
              slice_needed: bool = True) -> list[dict]:
    """Parse one contiguous run of pages, mapped back onto the original page numbers."""
    file_for_run = source
    if slice_needed and source.suffix.lower() == ".pdf":
        file_for_run = slice_pdf(source, pages, workdir, uuid.uuid4().hex[:6])
    pipeline = get_pipeline()
    results = []
    for page in pipeline.predict_iter(str(file_for_run), format_block_content=True):
        results.append(page)
        if len(results) >= max_pages:
            warnings.append("stopped_at_max_pages:" + str(max_pages))
            break
    if merge_tables and len(results) > 1:
        try:
            results = list(pipeline.restructure_pages(
                results, merge_tables=True, relevel_titles=True, concatenate_pages=False))
        except Exception as exc:  # noqa: BLE001
            warnings.append("restructure_failed:" + type(exc).__name__)
    entries = []
    for offset, page_res in enumerate(results):
        page_no = pages[offset] if offset < len(pages) else offset + 1
        blocks = page_blocks(page_res)
        entries.append({
            "page_no": page_no,
            "pipeline_page_index": res_of(page_res).get("page_index"),
            "blocks": blocks,
            "markdown": page_markdown(page_res, blocks, strip_image_refs_enabled()),
            "tables": html_tables(blocks),
        })
    return entries


async def parse_bytes(raw: bytes, name: str, pages: str, merge_tables: bool,
                      return_blocks: bool, max_pages: int):
    started = time.perf_counter()
    warnings: list[str] = []
    workdir = Path(tempfile.mkdtemp(prefix="ict-parse-"))
    try:
        stored = workdir / (Path(name).name or "upload.bin")
        stored.write_bytes(raw)
        suffix = stored.suffix.lower()
        if suffix in NATIVE_TABLE_SUFFIXES:
            return {"ok": False, "status": 415, "failure": "spreadsheet_is_native",
                    "hint": "read cells directly; never rasterise xlsx"}
        source = stored
        converted_from = None
        if suffix in OFFICE_SUFFIXES:
            try:
                source = soffice_to_pdf(stored, workdir)
                converted_from = suffix
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "status": 415, "failure": "office_convert:" + str(exc)}
        elif suffix not in IMAGE_SUFFIXES and suffix != ".pdf":
            return {"ok": False, "status": 415, "failure": "unsupported:" + (suffix or "no_extension")}

        document_pages = None
        wanted: list[int] = []
        if source.suffix.lower() == ".pdf":
            import pypdfium2 as pdfium

            document = pdfium.PdfDocument(str(source))
            document_pages = len(document)
            document.close()
            wanted = parse_range(pages, document_pages)

        entries: list[dict] = []
        if wanted:
            for run in contiguous_runs(wanted):
                if len(run) > max_pages:
                    warnings.append("run_capped:" + str(len(run)) + "->" + str(max_pages))
                    run = run[:max_pages]
                entries.extend(run_range(source, run, workdir, max_pages, merge_tables, warnings))
            entries.sort(key=lambda item: item["page_no"])
        else:
            every = list(range(1, (document_pages or max_pages) + 1))
            if document_pages and document_pages > max_pages:
                warnings.append("document_truncated:" + str(document_pages) + "->" + str(max_pages))
                every = every[:max_pages]
            entries = run_range(source, every, workdir, max_pages, merge_tables, warnings,
                                slice_needed=False)
        if not entries:
            return {"ok": False, "status": 422, "failure": "no_pages_parsed"}

        tables: list[dict] = []
        payload_pages: list[dict] = []
        for entry in entries:
            for index, html in enumerate(entry["tables"]):
                tables.append({"page_no": entry["page_no"], "index": index, "html": html})
            item = {"page_no": entry["page_no"], "markdown": entry["markdown"]}
            if return_blocks:
                item["blocks"] = entry["blocks"]
            payload_pages.append(item)

        return {
            "ok": True,
            "pages": payload_pages,
            "tables": tables,
            "meta": {
                "source_file": Path(name).name,
                "source_suffix": suffix,
                "converted_from": converted_from,
                "document_pages": document_pages,
                "parsed_pages": [item["page_no"] for item in payload_pages],
                "layout_device": env("DEVICE", "cpu"),
                "ms": int((time.perf_counter() - started) * 1000),
                "markdown_chars": sum(len(item["markdown"]) for item in payload_pages),
                "warnings": warnings,
            },
        }
    except Exception as exc:  # noqa: BLE001 - one broken file must not take the worker down
        return {"ok": False, "status": 500, "failure": type(exc).__name__ + ":" + str(exc)}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def build_app():
    application = FastAPI(title="ict-parse", version="0.2")
    token = env("ICT_PARSE_TOKEN", "")

    @application.get("/health")
    def health() -> dict:
        import paddle

        return {
            "ok": True,
            "paddle": paddle.__version__,
            "device": env("DEVICE", "cpu"),
            "vl_server_url": env("ICT_VL_SERVER_URL", "native"),
            "vl_max_concurrency": int(env("ICT_PARSE_MAX_CONCURRENCY", "16")),
            "cpu_threads": int(env("ICT_CPU_THREADS", "4")),
            "enable_mkldnn": env("ICT_ENABLE_MKLDNN", "0") == "1",
            "soffice": bool(shutil.which("soffice") or shutil.which("libreoffice")),
            "pipeline_loaded": _pipeline is not None,
            "auth_required": bool(token),
            "pid": os.getpid(),
        }

    @application.post("/parse")
    async def parse(file: UploadFile = File(...), pages: str = Form(""),
                    merge_tables: bool = Form(True), return_blocks: bool = Form(True),
                    max_pages: int = Form(30), x_parse_token: str = Form("")):
        if token and x_parse_token != token:
            return JSONResponse({"ok": False, "failure": "bad_token"}, status_code=401)
        produced = await parse_bytes(await file.read(), file.filename or "upload.bin",
                                     pages, merge_tables, return_blocks,
                                     max(1, min(max_pages, 200)))
        status = produced.get("status", 200)
        return JSONResponse(produced, status_code=status) if status != 200 else produced

    @application.post("/parse/base64")
    async def parse_b64(payload: dict):
        if token and str(payload.get("token") or "") != token:
            return JSONResponse({"ok": False, "failure": "bad_token"}, status_code=401)
        raw = base64.b64decode(payload.get("file_b64") or "")
        produced = await parse_bytes(raw, str(payload.get("name") or "upload.pdf"),
                                     str(payload.get("pages") or ""),
                                     bool(payload.get("merge_tables", True)),
                                     bool(payload.get("return_blocks", True)),
                                     int(payload.get("max_pages") or 30))
        status = produced.get("status", 200)
        return JSONResponse(produced, status_code=status) if status != 200 else produced

    return application


app = build_app()
