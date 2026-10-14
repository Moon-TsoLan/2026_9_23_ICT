"""Read-only replay of 50 saved results into the official exporter. Never creates a client."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from openpyxl import load_workbook
from ict.official import export_workbook, official_rows
from ict.schemas import MergedProjects, CandidateFile
from ict.steps.s07_normalize import normalize_candidates
from ict.catalog import Catalog


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=Path, default=Path("work/e2e-20261012/runtime/tenants/default/runs"))
    parser.add_argument("--output", type=Path, default=Path("work/official-compat-20261013"))
    args = parser.parse_args()
    source_files = [p for root in sorted(args.runs.iterdir()) for name in
                    ("01_announcement_understanding.json", "08_normalized_candidates.json", "09_merged_projects.json")
                    if (p := root / name).is_file()]
    hashes = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files}
    results = []
    normalized_count = 0
    for root in sorted(args.runs.iterdir()):
        path = root / "09_merged_projects.json"
        if not path.is_file():
            continue
        original = json.loads(path.read_text(encoding="utf-8"))
        merged = MergedProjects.model_validate(original)
        candidates = CandidateFile.model_validate_json((root / "08_normalized_candidates.json").read_bytes()).candidates
        normalized_count += len(normalize_candidates("offline", candidates, Catalog()).candidates)
        result = {"schema_version": "announcement-v2", "announcement_id": root.name,
                  "packages": [p.model_dump() for p in merged.projects]}
        results.append(result)
    data = export_workbook(results)
    book = load_workbook(BytesIO(data), data_only=True)
    rows = official_rows(results)
    assert book.sheetnames == ["总表", "标的表", "投标人表"]
    assert all(book[name].max_row - 1 == len(values) for name, values in rows.items())
    blue = sum(cell.fill.fgColor.rgb.endswith("1F4E79") for sheet in book for cell in sheet[1])
    assert blue == 21
    assert all(hashlib.sha256(p.read_bytes()).hexdigest() == digest for p, digest in hashes.items())
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "50例历史结果-官方三表兼容样例.xlsx").write_bytes(data)
    report = {"scope": "historical results compatibility only; no API/OCR/client/database calls; not a new accuracy run",
              "announcements": len(results), "packages": sum(len(r["packages"]) for r in results),
              "normalized_candidates": normalized_count, "sheets": {name: len(values) for name, values in rows.items()},
              "blue_headers": blue, "source_files_unchanged": len(hashes),
              "unknown_unit_names": sum(p.get("package_name") is None for r in results for p in r["packages"]),
              "note": "Old outputs have no newly extracted quote/review/type facts. They remain blank/unknown, never fabricated."}
    (args.output / "offline_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
