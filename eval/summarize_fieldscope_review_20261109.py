"""Join model-authored source labels to frozen real decisions; no business screening rules."""
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "work/attachment-fieldscope-review-20261109"
OLD = ROOT / "work/attachment-coverage-expanded-20261109/analysis38"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def aggregate():
    manifest = read(OUT / "manifest.json")
    old_rows = {r["page_key"]: r for r in read(OLD / "rows.json")}
    old_summary = read(OLD / "summary.json")
    old_manifest = read(OLD / "manifest.json")
    labels, duplicate_labels, invalid, partial_lines = {}, [], [], []
    for path in sorted((OUT / "reviews").glob("*.jsonl")):
        if path.name == "root_adjudications.jsonl":
            continue
        for line_no, text in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not text.strip():
                continue
            try:
                label = json.loads(text)
            except json.JSONDecodeError:
                partial_lines.append({"path": str(path), "line": line_no})
                continue
            key = label.get("unit_key")
            if label.get("target") not in (True, False, "uncertain") or label.get("reading") not in ("native", "ocr", "none", "uncertain"):
                invalid.append({"path": str(path), "line": line_no, "unit_key": key})
                continue
            if key in labels:
                duplicate_labels.append({"unit_key": key, "previous": labels[key]["review_source"], "next": path.name})
            labels[key] = {**label, "review_source": path.name}
    adjudications = OUT / "reviews/root_adjudications.jsonl"
    if adjudications.exists():
        for text in adjudications.read_text(encoding="utf-8").splitlines():
            if not text.strip(): continue
            label = json.loads(text)
            key = label["unit_key"]
            label["initial_review"] = labels.get(key)
            labels[key] = {**label, "review_source": adjudications.name}
    source_by_key = {}
    for source in old_manifest["files"]:
        key = hashlib.sha256((source["announcement_id"] + source["digest"]).encode()).hexdigest()[:16]
        source_by_key[key] = source
    rows, pending, images_not_viewed, incomplete_target_labels = [], [], [], []
    for case in manifest["cases"]:
        index = read(OUT / "cases" / case["announcement_id"] / "index.json")
        for key in index["units"]:
            unit = read(OUT / "units" / (key + ".json"))
            label, actual = labels.get(key), old_rows.get(key)
            source = source_by_key[unit["file_key"]]
            row = {k: unit[k] for k in ("unit_key", "announcement_id", "file_key", "file_id", "file_name", "source_path", "page_no", "unit_no", "coordinate_kind", "physical_page", "image", "has_image_content")}
            row["review"] = label
            if label and unit["has_image_content"] and label.get("inspected") not in ("image", "text_and_image"):
                images_not_viewed.append(key)
            if label and label["target"] is True and (label["reading"] not in ("native", "ocr") or not label.get("fields") or not label.get("evidence")):
                incomplete_target_labels.append(key)
            row["old_action"] = actual["action"] if actual else None
            row["old_method"] = actual["method"] if actual else None
            row["entered"] = bool(actual and actual["entered"])
            row["old_loss"] = actual.get("loss") if actual else "rule_name_ignore" if source["hard_ignored"] else "parse_page_budget"
            row["old_gate_reason"] = actual.get("gate_reason") if actual else None
            row["in_old_read_budget"] = bool(actual and actual["in_read_budget"] and not actual["hard_ignored"])
            row["selected_before_extraction_budget"] = bool(actual and actual["action"] in ("ocr", "extract"))
            row["reading_covered"] = bool(label and label["target"] is True and row["entered"] and
                                          (actual["action"] == "ocr" or label["reading"] == "native" and actual["action"] == "extract"))
            row["selected_reading_covered"] = bool(label and label["target"] is True and actual and
                                                   (actual["action"] == "ocr" or label["reading"] == "native" and actual["action"] == "extract"))
            row["wrong_native_only"] = bool(label and label["target"] is True and label["reading"] == "ocr" and row["entered"] and actual["action"] == "extract")
            row["unnecessary_ocr"] = bool(label and label["target"] is True and label["reading"] == "native" and row["entered"] and actual["action"] == "ocr")
            row["overread"] = bool(label and label["target"] is False and row["entered"])
            if not label: pending.append(key)
            rows.append(row)

    def metrics(part):
        reviewed = [r for r in part if r["review"]]
        target = [r for r in reviewed if r["review"]["target"] is True]
        losses = Counter("reading_mode_wrong" if r["wrong_native_only"] else r["old_loss"] or "unresolved_routing" for r in target if not r["reading_covered"])
        entered = [r for r in reviewed if r["entered"]]
        return {"source_units": len(part), "reviewed": len(reviewed), "pending": len(part)-len(reviewed),
                "targets": len(target), "targets_physical": sum(r["physical_page"] for r in target),
                "targets_logical": sum(not r["physical_page"] for r in target),
                "expected_reading": dict(Counter(r["review"]["reading"] for r in target)),
                "covered": sum(r["reading_covered"] for r in target),
                "selected_covered_before_extraction_budget": sum(r["selected_reading_covered"] for r in target),
                "covered_expected_reading": dict(Counter(r["review"]["reading"] for r in target if r["reading_covered"])),
                "misses": dict(losses), "wrong_native_only": sum(r["wrong_native_only"] for r in target),
                "unnecessary_ocr": sum(r["unnecessary_ocr"] for r in target),
                "uncertain": sum(r["review"]["target"] == "uncertain" for r in reviewed),
                "entered_reviewed": len(entered),
                "overread": dict(Counter(r["old_action"] for r in entered if r["review"]["target"] is False)),
                "overread_coordinates": dict(Counter(r["old_action"] + ("_physical" if r["physical_page"] else "_logical") for r in entered if r["review"]["target"] is False)),
                "entered_uncertain": sum(r["review"]["target"] == "uncertain" for r in entered),
                "implementation_gap_targets": sum(r["review"].get("implementation_gap", False) for r in target),
                "context_conflict_targets": sum(r["review"].get("context_conflict", False) for r in target),
                "decision_matrix": dict(Counter((r["review"]["reading"] if r["review"]["target"] is True else str(r["review"]["target"])) + " -> " + str(r["old_action"]) for r in reviewed))}

    cases = {case["announcement_id"]: {"project_name": case["project_name"], **metrics([r for r in rows if r["announcement_id"] == case["announcement_id"]])} for case in manifest["cases"]}
    selected = [r for r in rows if r["entered"]]
    report = {"cases": len(manifest["cases"]), "files": sum(c["files"] for c in manifest["cases"]),
              "all_source": metrics(rows), "old_read_budget": metrics([r for r in rows if r["in_old_read_budget"]]),
              "actual_old_entry": {"ocr": sum(r["old_action"] == "ocr" for r in selected),
                                   "extract": sum(r["old_action"] == "extract" for r in selected),
                                   "physical": sum(r["physical_page"] for r in selected),
                                   "logical": sum(not r["physical_page"] for r in selected)},
              "old_selected_before_extraction_budget": dict(Counter(r["old_action"] for r in rows if r["selected_before_extraction_budget"])),
              "old_image_gate_actions": old_summary["model_actions"],
              "image_gate_all": metrics([r for r in rows if r["old_method"] == "model_page"]),
              "image_gate_completed": metrics([r for r in rows if r["old_method"] == "model_page" and r["old_action"] in ("ocr", "extract", "skip")]),
              "old_entry_ocr": metrics([r for r in rows if r["entered"] and r["old_action"] == "ocr"]),
              "old_entry_extract": metrics([r for r in rows if r["entered"] and r["old_action"] == "extract"]),
              "actual_old_entry_coordinates": dict(Counter(r["old_action"] + ("_physical" if r["physical_page"] else "_logical") for r in selected)),
              "by_case": cases, "source_errors": manifest["errors"],
              "duplicate_labels": duplicate_labels, "invalid_labels": invalid, "partial_lines": partial_lines,
              "unknown_label_keys": sorted(set(labels)-{r["unit_key"] for r in rows}),
              "images_not_viewed": images_not_viewed, "incomplete_target_labels": incomplete_target_labels,
              "complete_page_review": not pending and not invalid and not partial_lines and not images_not_viewed and not incomplete_target_labels,
              "new_api_calls": 0, "new_ocr_calls": 0,
              "label_status": "gpt-6-luna source review plus root review/adjudication; not human gold",
              "entry_status": "Frozen real page decisions and existing zero-network production scheduling replay; no actual OCR/extraction requests"}
    assert report["actual_old_entry"]["ocr"] == 67 and report["actual_old_entry"]["extract"] == 1146
    write(OUT / "comparison_rows.json", rows)
    write(OUT / "summary.json", report)
    write(OUT / "pending_units.json", pending)
    print(json.dumps({k: v for k, v in report.items() if k in ("cases", "files", "all_source", "actual_old_entry", "complete_page_review", "duplicate_labels", "invalid_labels")}, ensure_ascii=False))


if __name__ == "__main__":
    aggregate()
