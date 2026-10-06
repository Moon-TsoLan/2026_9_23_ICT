"""Command line entry for the index builder, one announcement, or a batch."""

from __future__ import annotations

import argparse

from ict.index.build import build_index
from ict.pipeline import run_announcement, run_batch


def main() -> None:
    parser = argparse.ArgumentParser(description="ICT main-route extraction")
    sub = parser.add_subparsers(dest="command", required=True)
    index = sub.add_parser("index")
    index.add_argument("announcement_id")
    run = sub.add_parser("run")
    run.add_argument("announcement_id")
    batch = sub.add_parser("batch")
    batch.add_argument("announcement_ids", nargs="+")
    batch.add_argument("--workers", type=int, default=None,
                       help="同时处理的公告数（默认取 ICT_ANNOUNCEMENT_WORKERS，设 1 即老的串行循环）")
    args = parser.parse_args()
    if args.command == "index":
        built = build_index(args.announcement_id)
        print(f"{args.announcement_id} files {len(built.files)}")
        return
    if args.command == "batch":
        reports = run_batch(args.announcement_ids, workers=args.workers)
        for announcement_id in args.announcement_ids:
            report = reports.get(announcement_id)
            if report is None:
                print(f"{announcement_id} missing")
                continue
            print(f"{report.run_id} {report.status} projects {report.counts.get('projects')} "
                  f"cobs {report.counts.get('final_cobs')} {report.duration_ms / 1000:.1f}s")
        return
    report = run_announcement(args.announcement_id)
    print(f"{report.run_id} {report.status} projects {report.counts.get('projects')} cobs {report.counts.get('final_cobs')}")


if __name__ == "__main__":
    main()
