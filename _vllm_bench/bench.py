import argparse
import asyncio
import base64
import io
import json
import statistics
import time
from urllib.request import urlopen

import pypdfium2 as pdfium
from openai import AsyncOpenAI

BASE_URL = "https://u1196524-wjps-a6171b48.bjb2.seetacloud.com:8443/v1"
ROOT_URL = "https://u1196524-wjps-a6171b48.bjb2.seetacloud.com:8443"
MODEL = "PaddlePaddle/PaddleOCR-VL-1.6"


def render_page(pdf_path, page_index, dpi):
    doc = pdfium.PdfDocument(pdf_path)
    page = doc[page_index]
    img = page.render(scale=dpi / 72).to_pil()
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    raw = buf.getvalue()
    return "data:image/png;base64," + base64.b64encode(raw).decode(), img.size, len(raw)


def make_payload(data_uri, prompt):
    return [
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": data_uri}},
                {"type": "text", "text": prompt},
            ],
        }
    ]


async def call_once(client, messages, max_tokens):
    t0 = time.perf_counter()
    resp = await client.chat.completions.create(
        model=MODEL, messages=messages, max_tokens=max_tokens, temperature=0
    )
    dt = time.perf_counter() - t0
    usage = resp.usage
    return {
        "latency_s": dt,
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "chars": len(resp.choices[0].message.content or ""),
        "finish_reason": resp.choices[0].finish_reason,
    }


async def call_stream(client, messages, max_tokens):
    t0 = time.perf_counter()
    ttft = None
    chunks = 0
    text_len = 0
    usage = None
    stream = await client.chat.completions.create(
        model=MODEL,
        messages=messages,
        max_tokens=max_tokens,
        temperature=0,
        stream=True,
        stream_options={"include_usage": True},
    )
    async for chunk in stream:
        if chunk.usage is not None:
            usage = chunk.usage
        if chunk.choices:
            piece = getattr(chunk.choices[0].delta, "content", None)
            if piece:
                if ttft is None:
                    ttft = time.perf_counter() - t0
                chunks += 1
                text_len += len(piece)
    total = time.perf_counter() - t0
    ct = getattr(usage, "completion_tokens", None) if usage else None
    tpot = (total - ttft) / max(ct - 1, 1) if (ttft is not None and ct) else None
    return {
        "ttft_s": ttft,
        "total_s": total,
        "completion_tokens": ct,
        "prompt_tokens": getattr(usage, "prompt_tokens", None) if usage else None,
        "chunks": chunks,
        "chars": text_len,
        "tpot_s": tpot,
    }


async def rtt_probe(n):
    out = []
    for _ in range(n):
        t0 = time.perf_counter()
        urlopen(ROOT_URL + "/v1/models", timeout=20).read()
        out.append(time.perf_counter() - t0)
    return out


async def concurrency_level(client, messages, level, requests_total, max_tokens):
    sem = asyncio.Semaphore(level)

    async def worker():
        async with sem:
            t0 = time.perf_counter()
            try:
                r = await call_once(client, messages, max_tokens)
                r["ok"] = True
            except Exception as exc:
                r = {
                    "ok": False,
                    "latency_s": time.perf_counter() - t0,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            return r

    t0 = time.perf_counter()
    results = await asyncio.gather(*(worker() for _ in range(requests_total)))
    wall = time.perf_counter() - t0
    ok = [r for r in results if r["ok"]]
    lat = [r["latency_s"] for r in ok]
    ctoks = sum(r.get("completion_tokens") or 0 for r in ok)
    ptoks = sum(r.get("prompt_tokens") or 0 for r in ok)
    lat_sorted = sorted(lat)
    p95 = lat_sorted[min(len(lat_sorted) - 1, int(0.95 * len(lat_sorted)))] if lat else None
    return {
        "concurrency": level,
        "requests": requests_total,
        "ok": len(ok),
        "failed": len(results) - len(ok),
        "wall_s": wall,
        "throughput_req_s": len(ok) / wall if wall else None,
        "out_tokens_per_s": ctoks / wall if wall else None,
        "latency_avg_s": statistics.mean(lat) if lat else None,
        "latency_median_s": statistics.median(lat) if lat else None,
        "latency_p95_s": p95,
        "latency_min_s": min(lat) if lat else None,
        "latency_max_s": max(lat) if lat else None,
        "completion_tokens_total": ctoks,
        "prompt_tokens_total": ptoks,
        "errors": [r["error"] for r in results if not r["ok"]][:5],
    }


def sample_metrics():
    try:
        text = urlopen(ROOT_URL + "/metrics", timeout=20).read().decode("utf-8", "ignore")
    except Exception as exc:
        return {"error": str(exc)}
    keys = {
        "vllm:num_requests_running": "running",
        "vllm:num_requests_waiting": "waiting",
        "vllm:kv_cache_usage_perc": "kv_cache_perc",
    }
    out = {}
    for line in text.splitlines():
        for k, name in keys.items():
            if line.startswith(k + "{"):
                try:
                    out[name] = float(line.rsplit(" ", 1)[1])
                except ValueError:
                    pass
    return out


async def metrics_sampler(stop_event, samples, interval=1.5):
    while not stop_event.is_set():
        samples.append({"t": time.time(), **sample_metrics()})
        await asyncio.sleep(interval)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--page", type=int, default=0)
    ap.add_argument("--dpi", type=int, default=200)
    ap.add_argument("--prompt", default="OCR:")
    ap.add_argument("--max-tokens", type=int, default=4096)
    ap.add_argument("--single", type=int, default=3)
    ap.add_argument("--levels", default="")
    ap.add_argument("--out", default="result.json")
    ap.add_argument("--total", type=int, default=16)
    args = ap.parse_args()

    data_uri, size, raw_len = render_page(args.pdf, args.page, args.dpi)
    messages = make_payload(data_uri, args.prompt)
    print("[info] page %dx%d px, PNG %.2f MB, base64 %.2f MB, prompt=%r" % (
        size[0], size[1], raw_len / 1048576.0, len(data_uri) / 1048576.0, args.prompt), flush=True)

    client = AsyncOpenAI(base_url=BASE_URL, api_key="null", timeout=900, max_retries=0)
    report = {"dpi": args.dpi, "page_size": size, "png_mb": raw_len / 1048576.0,
              "prompt": args.prompt, "max_tokens": args.max_tokens,
              "metrics_before": sample_metrics()}

    rtts = await rtt_probe(5)
    report["rtt"] = {"samples": rtts, "median_s": statistics.median(rtts)}
    print("[rtt] /v1/models median round trip %.0f ms" % (statistics.median(rtts) * 1000), flush=True)

    if args.single:
        singles = []
        for i in range(args.single):
            r = await call_once(client, messages, args.max_tokens)
            singles.append(r)
            print("[single %d/%d] %.2fs prompt=%s out=%s chars=%s finish=%s" % (
                i + 1, args.single, r["latency_s"], r["prompt_tokens"],
                r["completion_tokens"], r["chars"], r["finish_reason"]), flush=True)
        report["single"] = singles

    try:
        s = await call_stream(client, messages, args.max_tokens)
        report["stream"] = s
        print("[stream] TTFT %.2fs total %.2fs out=%s TPOT=%.3fs" % (
            s["ttft_s"] or -1, s["total_s"], s["completion_tokens"], s["tpot_s"] or -1), flush=True)
    except Exception as exc:
        report["stream"] = {"error": "%s: %s" % (type(exc).__name__, exc)}
        print("[stream] failed: %s" % exc, flush=True)

    if args.levels:
        levels = [int(x) for x in args.levels.split(",")]
        report["concurrency"] = []
        report["metrics_during"] = []
        for lv in levels:
            total = min(2 * lv, args.total)
            stop = asyncio.Event()
            samples = []
            sampler = asyncio.create_task(metrics_sampler(stop, samples))
            res = await concurrency_level(client, messages, lv, total, args.max_tokens)
            stop.set()
            await sampler
            report["concurrency"].append(res)
            report["metrics_during"].append({"concurrency": lv, "samples": samples})
            print("[conc %d] ok %d/%d wall %.2fs thr %.2f req/s out %.1f tok/s lat med %.2fs p95 %.2fs max %.2fs" % (
                lv, res["ok"], res["requests"], res["wall_s"], res["throughput_req_s"],
                res["out_tokens_per_s"], res["latency_median_s"], res["latency_p95_s"],
                res["latency_max_s"]), flush=True)
            if res["errors"]:
                print("      errors: %s" % res["errors"], flush=True)

    report["metrics_after"] = sample_metrics()
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print("[done] saved %s" % args.out, flush=True)


if __name__ == "__main__":
    asyncio.run(main())

