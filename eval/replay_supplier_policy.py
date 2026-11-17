"""Offline policy-only comparison; source outputs and gold files are never rewritten."""
from __future__ import annotations
import argparse
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ict.official import HEADERS, official_rows
from ict.schemas import MergedProjects, Project
from ict.supplier_policy import finalize_suppliers


def compare(runs, output):
    evaluation = ROOT / "work/e2e-20261017-replays/revised-evaluation"
    spec = importlib.util.spec_from_file_location("frozen_policy_score", evaluation / "score.py")
    score = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(score)
    gold_path = evaluation / "gold_snapshot.json"
    gold = json.loads(gold_path.read_text(encoding="utf-8"))
    source_paths = [runs / aid / "projects.json" for aid in gold["ids"]]
    assert all(path.is_file() for path in source_paths), "Requires all 50 existing results"
    audit_paths = [runs / aid / "09_merged_projects.json" for aid in gold["ids"]]
    tracked = source_paths + [path for path in audit_paths if path.is_file()] + [gold_path, Path(gold["source"])]
    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    before_hashes = {str(path): digest(path) for path in tracked}
    assert digest(Path(gold["source"])) == gold["sha256"], "Frozen gold source changed"
    original, revised, changes = [], [], []
    for path, audit_path in zip(source_paths, audit_paths):
        data = json.loads(path.read_text(encoding="utf-8"))
        aid = path.parent.name
        if isinstance(data, list):
            data = {"announcement_id": aid, "packages": data}
        data["announcement_id"] = aid
        original.append(data)
        updated = deepcopy(data)
        audit = json.loads(audit_path.read_text(encoding="utf-8")) if audit_path.is_file() else {}
        audit_projects = {p["project_id"]: p for p in audit.get("projects", [])}
        projects = [Project.model_validate(package | {"provenance": audit_projects.get(package["project_id"], {}).get("provenance")})
                    for package in updated["packages"]]
        merged = MergedProjects(run_id=aid, status="success", projects=projects,
            unresolved=audit.get("unresolved", []), conflicts=audit.get("conflicts", []),
            sub_merge_decisions=audit.get("sub_merge_decisions", []))
        finalize_suppliers(merged)
        changes.extend({"announcement_id": aid, **entry} for entry in merged.field_bindings)
        updated["packages"] = [project.model_dump(exclude={"provenance"}) for project in merged.projects]
        revised.append(updated)

    # Historical manual row indexes belong to their original output order. The two policy
    # arms here use identical exact identity matching, without reusing those row indexes.
    manual = []
    def metrics(results):
        cells = []
        for name, rows in official_rows(results).items():
            got = [{"row": index + 2, "values": dict(zip(HEADERS[name], row))} for index, row in enumerate(rows)]
            definition = gold["sheets"][name]
            values, _, _ = score.cells_for(name, definition["rows"], got, definition["blue_headers"], manual)
            cells.extend(values)
        return {"by_sheet": {name: score.metrics([cell for cell in cells if cell["sheet"] == name]) for name in gold["sheets"]},
                "by_policy_field": {field: score.metrics([cell for cell in cells if cell["sheet"] == "投标人表" and cell["field"] == field])
                                    for field in ("报价（万元）", "通过审查")}}
    report = {"experiment": "policy_only_no_model_no_ocr", "case_count": len(original),
        "audit_files_available": sum(path.is_file() for path in audit_paths),
        "limitation": "Exact identity alignment in both arms; historical manual row indexes are not reused. Missing merge audits cannot identify past unresolved conflicts. This is a policy-only replay, not a new extraction quality result.",
        "before": metrics(original), "after": metrics(revised), "changes": changes,
        "source_sha256": before_hashes}
    assert all(digest(Path(path)) == value for path, value in before_hashes.items())
    output.mkdir(parents=True, exist_ok=True)
    (output / "policy_comparison.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "policy_results.json").write_text(json.dumps(revised, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=Path, default=ROOT / "work/e2e-20261017-replays/revised-evaluation/runs")
    parser.add_argument("--output", type=Path, default=ROOT / "work/front-half-20261108/policy-only")
    args = parser.parse_args()
    report = compare(args.runs, args.output)
    print(json.dumps({"case_count": report["case_count"], "policy_changes": len(report["changes"]),
                      "audit_files_available": report["audit_files_available"]}))
