"""Record the model once, then replay it as often as you like.

    python eval/run_replay.py record t20260202_26140146 t20260512_26551404
    python eval/run_replay.py replay t20260202_26140146 ... --out work/_replay/serial
    python eval/run_replay.py diff work/_replay/serial work/_replay/parallel

`record` needs the real endpoint. `replay` and `diff` need nothing but the cassette, so a
regression run costs no API calls and, more importantly, has no model noise in it: two replay
runs of the same cassette must agree byte for byte apart from timings. That is what makes a
concurrency change reviewable.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RUNS = REPO / "work" / "runs"
sys.path.insert(0, str(REPO / "src"))

from ict import replay as cassettes  # noqa: E402
from ict.index.build import build_index  # noqa: E402
from ict.pipeline import run_batch  # noqa: E402

# Timing fields differ between any two runs by construction and are not evidence of anything.
VOLATILE = {"duration_ms", "created_at", "updated_at", "started_at", "ended_at", "ms", "latency_ms"}


def _strip(node):
    if isinstance(node, dict):
        return {key: _strip(value) for key, value in node.items() if key not in VOLATILE}
    if isinstance(node, list):
        return [_strip(value) for value in node]
    return node


def _differences(left, right, path: str = "", out: list | None = None) -> list:
    out = [] if out is None else out
    if isinstance(left, dict) and isinstance(right, dict):
        for key in sorted(set(left) | set(right)):
            if key not in left:
                out.append((path + "/" + key, "<absent>", "<added>"))
            elif key not in right:
                out.append((path + "/" + key, "<removed>", "<absent>"))
            else:
                _differences(left[key], right[key], path + "/" + key, out)
    elif isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            out.append((path + "/len", len(left), len(right)))
        for index, (one, other) in enumerate(zip(left, right)):
            _differences(one, other, "%s[%d]" % (path, index), out)
    elif left != right:
        out.append((path, left, right))
    return out


def compare(dir_a: Path, dir_b: Path, ids: list[str] | None = None) -> int:
    names = sorted({path.name for path in dir_a.iterdir() if path.is_dir()} |
                   {path.name for path in dir_b.iterdir() if path.is_dir()}) if dir_a.is_dir() and dir_b.is_dir() else []
    names = ids or names
    bad = 0
    for name in names:
        left, right = dir_a / name, dir_b / name
        files = (sorted({path.name for path in left.iterdir()} | {path.name for path in right.iterdir()})
                 if left.is_dir() and right.is_dir() else [])
        if not files:
            print("  %-26s MISSING on one side" % name)
            bad += 1
            continue
        changed: list[str] = []
        for filename in files:
            one, other = left / filename, right / filename
            if not (one.exists() and other.exists()):
                changed.append(filename + "(one side)")
                continue
            try:
                parsed_one = json.loads(one.read_text(encoding="utf-8"))
                parsed_other = json.loads(other.read_text(encoding="utf-8"))
            except Exception:
                changed.append(filename + "(not json)")
                continue
            diffs = _differences(_strip(parsed_one), _strip(parsed_other))
            if diffs:
                changed.append(filename)
                for where, was, now in diffs[:3]:
                    print("      %s %s: %r -> %r" % (filename, where, was, now))
        print("  %-26s %s" % (name, "IDENTICAL" if not changed else "DIFFERS: " + ", ".join(changed)))
        bad += 1 if changed else 0
    return bad


def _run(ids: list[str], state: str, out: Path | None = None, workers: int | None = None) -> int:
    os.environ["ICT_LLM_MODE"] = state
    bad = 0
    for announcement_id in ids:
        try:
            build_index(announcement_id)
        except Exception as exc:  # noqa: BLE001 - one bad announcement must not hide the rest
            print("  %-26s INDEX ERROR %s: %s" % (announcement_id, type(exc).__name__, exc))
            bad += 1
    started = time.perf_counter()
    try:
        reports = run_batch(ids, workers=workers)
    except Exception as exc:  # noqa: BLE001
        print("  batch failed: %s: %s" % (type(exc).__name__, exc))
        return bad + 1
    wall = time.perf_counter() - started
    for announcement_id in ids:
        report = reports.get(announcement_id)
        if report is None:
            bad += 1
            continue
        print("  %-26s %-7s %5.1fs calls=%-3d failures=%s" % (
            announcement_id, report.status, report.duration_ms / 1000.0,
            sum(report.llm_calls.values()), [item["failure_code"] for item in report.failure_summary]))
        if out is not None:
            shutil.rmtree(out / announcement_id, ignore_errors=True)
            shutil.copytree(RUNS / announcement_id, out / announcement_id)
    print("  %-26s wall %.1fs (workers=%s)" % ("", wall, workers or "default"))
    if state == "replay":
        missed = cassettes.misses()
        if missed:
            bad += len(missed)
            print("\n回放缺 %d 次调用（说明这些步骤的输入与录制时不同）：" % len(missed))
            for item in missed[:20]:
                print("   %-28s %-22s key=%s" % (item["step"], item["prompt_version"], item["key"]))
    return bad


def main() -> None:
    parser = argparse.ArgumentParser(description="record / replay the model boundary")
    sub = parser.add_subparsers(dest="command", required=True)
    record = sub.add_parser("record")
    record.add_argument("ids", nargs="+")
    record.add_argument("--workers", type=int, default=None,
                        help="announcements in flight (default: ICT_ANNOUNCEMENT_WORKERS)")
    play = sub.add_parser("replay")
    play.add_argument("ids", nargs="+")
    play.add_argument("--out", required=True)
    play.add_argument("--workers", type=int, default=None,
                      help="announcements in flight (default: ICT_ANNOUNCEMENT_WORKERS)")
    diff = sub.add_parser("diff")
    diff.add_argument("dir_a")
    diff.add_argument("dir_b")
    diff.add_argument("--ids", nargs="*")
    args = parser.parse_args()

    if args.command == "diff":
        bad = compare(Path(args.dir_a), Path(args.dir_b), args.ids)
        print("\n结果：%s" % ("一致" if not bad else "%d 处不一致" % bad))
        raise SystemExit(1 if bad else 0)

    path = cassettes.cassette_path()
    before = cassettes.record_count(path) if path.exists() else 0
    out = Path(args.out) if args.command == "replay" else None
    if out is not None:
        out.mkdir(parents=True, exist_ok=True)
    print("cassette %s（现有 %d 条）" % (path, before))
    bad = _run(args.ids, "record" if args.command == "record" else "replay", out,
               workers=args.workers)
    if args.command == "record":
        print("cassette 现在 %d 条（新增 %d）" % (cassettes.record_count(path), cassettes.record_count(path) - before))
    raise SystemExit(1 if bad else 0)


if __name__ == "__main__":
    main()
