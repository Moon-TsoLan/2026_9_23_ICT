"""Run the fixed 10-announcement set and write a comparison baseline."""

from __future__ import annotations

import json
import traceback
from pathlib import Path

from ict.config import OFFICIAL_SEVEN, REPO_ROOT
from ict.index.build import build_index
from ict.pipeline import run_announcement

MANIFEST = REPO_ROOT / "eval" / "benchmark10.json"
OUT = REPO_ROOT / "work" / "benchmark10"


def _coverage(projects: list[dict]) -> dict[str, float]:
    cobs = [cob for project in projects for cob in project.get("cobs") or []]
    total = len(cobs)
    coverage = {}
    for key in OFFICIAL_SEVEN:
        filled = sum(1 for cob in cobs if cob.get(key) not in (None, ""))
        coverage[key] = 0 if total == 0 else round(filled / total, 3)
    return {"cob_count": total, "sub_count": sum(len(project.get("subs") or []) for project in projects), "fields": coverage}


def main() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for item in manifest["announcements"]:
        announcement_id = item["announcement_id"]
        print(f"start {announcement_id} {item['role']}", flush=True)
        row = {"announcement_id": announcement_id, "role": item["role"], "why": item["why"]}
        try:
            index = build_index(announcement_id)
            row["indexed_files"] = len(index.files)
            report = run_announcement(announcement_id)
            projects_path = REPO_ROOT / "work" / "runs" / announcement_id / "projects.json"
            projects = json.loads(projects_path.read_text(encoding="utf-8")) if projects_path.exists() else []
            row.update(
                {
                    "status": report.status,
                    "duration_ms": report.duration_ms,
                    "counts": report.counts,
                    "llm_calls": report.llm_calls,
                    "failure_summary": report.failure_summary,
                    "packages": [
                        {
                            "package_no": project.get("package_no"),
                            "cobs": len(project.get("cobs") or []),
                            "subs": len(project.get("subs") or []),
                            "package_total_amount": project.get("package_total_amount"),
                        }
                        for project in projects
                    ],
                    "coverage": _coverage(projects),
                }
            )
        except Exception as exc:
            row["status"] = "error"
            row["error"] = f"{type(exc).__name__}: {exc}"
            row["traceback"] = traceback.format_exc()
        rows.append(row)
        (OUT / "baseline.json").write_text(
            json.dumps({"manifest": manifest["name"], "results": rows}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"done {announcement_id} {row.get('status')} cobs {row.get('coverage', {}).get('cob_count')}", flush=True)
    print(f"baseline {OUT / 'baseline.json'}", flush=True)


if __name__ == "__main__":
    main()
