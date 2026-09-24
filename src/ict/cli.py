"""Command line entry for the index builder and one-announcement run."""

from __future__ import annotations

import argparse

from ict.index.build import build_index
from ict.pipeline import run_announcement


def main() -> None:
    parser = argparse.ArgumentParser(description="ICT main-route extraction")
    sub = parser.add_subparsers(dest="command", required=True)
    index = sub.add_parser("index")
    index.add_argument("announcement_id")
    run = sub.add_parser("run")
    run.add_argument("announcement_id")
    args = parser.parse_args()
    if args.command == "index":
        built = build_index(args.announcement_id)
        print(f"{args.announcement_id} files {len(built.files)}")
        return
    report = run_announcement(args.announcement_id)
    print(f"{report.run_id} {report.status} projects {report.counts.get('projects')} cobs {report.counts.get('final_cobs')}")


if __name__ == "__main__":
    main()
