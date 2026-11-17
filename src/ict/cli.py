"""Command line entry for the index builder, one announcement, or a batch."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ict.index.build import build_index
from ict.pipeline import run_announcement, run_batch


def main() -> None:
    parser = argparse.ArgumentParser(description="ICT main-route extraction")
    sub = parser.add_subparsers(dest="command", required=True)
    index = sub.add_parser("index")
    index.add_argument("announcement_id")
    run = sub.add_parser("run")
    run.add_argument("announcement_id")
    front = sub.add_parser("freeze-front", help="HTML 建树与附件离线首判，冻结开发样例")
    front.add_argument("announcement_id")
    front.add_argument("output", type=Path)
    front.add_argument("--ocr-json", type=Path, help="已识别的离线文本，按相对文件名和页号索引")
    units = sub.add_parser("merge-units", help="从冻结的结果树判断采购单元归属")
    units.add_argument("tree", type=Path)
    units.add_argument("output", type=Path)
    delta = sub.add_parser("package-delta", help="在已归包的树上运行现有包内合并")
    delta.add_argument("tree", type=Path)
    delta.add_argument("output", type=Path)
    fill = sub.add_parser("fill-reference", help="只用第三类文件给已有对象补空")
    fill.add_argument("tree", type=Path)
    fill.add_argument("merged", type=Path)
    fill.add_argument("output", type=Path)
    batch = sub.add_parser("batch")
    batch.add_argument("announcement_ids", nargs="+")
    batch.add_argument("--workers", type=int, default=None,
                       help="前半程处理名额（默认取 ICT_ANNOUNCEMENT_WORKERS；接续独立）")
    args = parser.parse_args()
    if args.command in {"freeze-front", "merge-units", "package-delta", "fill-reference"}:
        from ict.llm import build_client
        from ict.redesign import freeze_front, merge_package_objects, save_new
        from ict.reference_fill import fill_reference_fields
        from ict.schemas import MergedProjects
        from ict.unit_merge import merge_units
        if args.output.exists():
            parser.error("固定输出已存在")
        def read(path):
            return json.loads(path.read_text(encoding="utf-8"))
        if args.command == "freeze-front":
            data = freeze_front(args.announcement_id, build_client(),
                                ocr_by_file=read(args.ocr_json) if args.ocr_json else None)
        elif args.command == "merge-units":
            data = merge_units(read(args.tree), build_client())
        elif args.command == "package-delta":
            client = build_client()
            if client is None:
                parser.error("包内 delta 需要配置模型")
            data = merge_package_objects(read(args.tree), client).model_dump()
        else:
            data = fill_reference_fields(read(args.tree), MergedProjects.model_validate(read(args.merged))).model_dump()
        save_new(args.output, data)
        return
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
