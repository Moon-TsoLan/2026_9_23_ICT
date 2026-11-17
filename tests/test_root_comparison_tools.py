"""Downloaded exports and run archives use the established scorer and distinct time meanings."""
import csv
import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import compare_results as quality
import compare_times as timing
from ict.official import HEADERS, official_rows, export_workbook


@pytest.fixture
def specimen():
    result = {"announcement_id": "test", "project_name": "项目", "purchaser": "采购人", "packages": [
        {"project_id": "p1", "package_no": "1", "package_kind": "contract_package", "source_package_no": "1",
         "package_name": "合同包1", "package_total_amount": 100000,
         "cobs": [{"object_name": "设备", "brand": "品牌", "spec_model": "型号", "quantity": 2,
                   "unit_price": 50000, "total_price": 100000, "unit": "台", "product_supplier": "厂商,公司\n分部"}],
         "subs": [{"supplier_name": "供应商", "is_winner": True, "bid_amount": 100000,
                   "review_status": "passed", "score": 92.4}]}]}
    sheets = {n: [{"row": i + 2, "values": dict(zip(HEADERS[n], r)), "filled_cells": {}}
                   for i, r in enumerate(rows)] for n, rows in official_rows([result]).items()}
    gold = {"ids": ["test"], "sheets": {n: {"headers": HEADERS[n], "blue_headers": quality.blue_headers(n),
                                            "rows": deepcopy(rows)} for n, rows in sheets.items()}}
    return result, sheets, gold


def csv_export(path, name, rows, headers=None, encoding="utf-8-sig"):
    headers = headers or HEADERS[name]
    with path.open("w", encoding=encoding, newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows([{h: row["values"].get(h) for h in headers} for row in rows])


def test_csv_and_xlsx_equal_existing_run_scoring(tmp_path, specimen):
    result, sheets, gold = specimen
    csv_dir = tmp_path / "csv"
    csv_dir.mkdir()
    for name, rows in sheets.items():
        csv_export(csv_dir / f"{name}.csv", name, rows, encoding="gb18030")
    xlsx = tmp_path / "download.xlsx"
    xlsx.write_bytes(export_workbook([result]))
    run = tmp_path / "runs/test"
    run.mkdir(parents=True)
    (run / "projects.json").write_text(json.dumps(result), encoding="utf-8")
    reference = quality.score(run.parent, ["test"], gold)
    for path in (csv_dir, xlsx):
        loaded, cols = quality.read_export(path)
        got, ids, unknown = quality.evaluate_export(loaded, cols, gold)
        assert ids == ["test"] and unknown == []
        assert got["metrics"] == reference["metrics"]
        assert got["gray_metrics"] == reference["gray_metrics"]
        assert all(m["accuracy_gold_slots"]["rate"] == 1 for m in got["metrics"].values())
    assert quality.read_export(csv_dir)[0]["标的表"][0]["values"]["产品供应商"] == "厂商,公司\n分部"


def test_one_csv_excludes_unprovided_tables_and_gray_columns_but_counts_missing_rows(tmp_path, specimen):
    _, sheets, gold = specimen
    second = deepcopy(gold["sheets"]["标的表"]["rows"][0])
    second["row"] = 3
    second["values"]["产品服务名称"] = "第二设备"
    gold["sheets"]["标的表"]["rows"].append(second)
    path = tmp_path / "single.csv"
    csv_export(path, "标的表", sheets["标的表"], quality.blue_headers("标的表"))
    loaded, cols = quality.read_export(path)
    got, _, _ = quality.evaluate_export(loaded, cols, gold)
    assert list(got["metrics"]) == ["标的表"]
    assert got["alignment"]["标的表"]["missing_rows"] == 1
    assert got["metrics"]["标的表"]["gold_slots"] == 2 * len(quality.blue_headers("标的表"))
    assert got["gray_metrics"]["标的表"]["gold_slots"] == 0
    assert got["gray_metrics"]["标的表"]["accuracy_gold_slots"]["rate"] is None


def test_inferred_policy_and_blue_gray_regression(tmp_path, monkeypatch, specimen):
    _, sheets, gold = specimen
    path, old = tmp_path / "current.csv", tmp_path / "old.csv"
    csv_export(old, "标的表", sheets["标的表"])
    sheets["标的表"][0]["values"].update({"品牌": "错误品牌", "单位": "套", "规格型号": None})
    # Spec is column G in official COB headers; retain the existing colored-cell exemption.
    gold["sheets"]["标的表"]["rows"][0]["filled_cells"] = {"G": True}
    csv_export(path, "标的表", sheets["标的表"])
    monkeypatch.setattr(quality, "gold_snapshot", lambda p: gold)
    fake_gold = tmp_path / "gold"
    fake_gold.write_text("gold", encoding="utf-8")
    output = tmp_path / "quality"
    got = quality.compare(path, output, fake_gold, old)
    assert got["metrics"]["标的表"]["allowed_empty_inferred"] == 1
    regression = json.loads((output / "regression.json").read_text(encoding="utf-8"))
    assert regression["counts"] == {"regression": 1}
    assert regression["gray_counts"] == {"regression": 1}
    assert (output / "report.md").exists() and (output / "field_metrics.csv").exists()


def archive(root, cases, *, window=20, cached=False):
    for aid, seconds, stages in cases:
        directory = root / "runs" / aid
        directory.mkdir(parents=True)
        (directory / "10_run_report.json").write_text(json.dumps({"announcement_id": aid, "status": "partial",
            "duration_ms": seconds * 1000, "model_calls": [
                {"step": "merge_candidates", "latency_ms": 1250, "usage": {"prompt_tokens": 10}},
                {"step": "merge_candidates", "latency_ms": 2250, "usage": {"prompt_tokens": 20}}]}), encoding="utf-8")
        (directory / "run_state.json").write_text(json.dumps({"announcement_id": aid, "status": "partial",
            "created_at": "2026-11-12T10:00:00+08:00", "steps": [
                {"step": step, "started_at": "2026-11-12T10:00:00+08:00", "ended_at": f"2026-11-12T10:00:{value:02d}+08:00"}
                for step, value in stages.items()]}), encoding="utf-8")
    (root / "manifest.json").write_text(json.dumps({"ids": [r[0] for r in cases], "start_time": "2026-11-12 10:00:00"}), encoding="utf-8")
    (root / "integrity.json").write_text(json.dumps({"end_time": f"2026-11-12 10:00:{window:02d}"}), encoding="utf-8")
    (root / "progress.json").write_text(json.dumps([{"cached_completed": cached}]), encoding="utf-8")


def test_times_compare_common_cases_without_summing_concurrent_wall_time(tmp_path):
    new, old = tmp_path / "new", tmp_path / "old"
    archive(new, [("a", 10.125, {"merge_candidates": 5}), ("b", 20.5, {"bind_packages": 2, "merge_candidates": 7})], window=22)
    archive(old, [("a", 20.25, {"merge_candidates": 10}), ("b", 30, {"merge_candidates": 15}),
                  ("old_only", 5, {})], window=45)
    out = tmp_path / "time_report"
    got = timing.compare(new, old / "runs", out)
    assert got["common_ids"] == ["a", "b"]
    assert got["current_common_latency"]["sum_s"] == 30.625
    assert got["current"]["recorded_batch_window_s"] == 22
    assert got["batch_recorded_window_change_fraction"] is None  # different case sets
    with (out / "stages.csv").open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert next(r for r in rows if r["step"] == "bind_packages")["baseline_s"] == ""
    assert got["current"]["cases"]["a"]["usage"]["prompt_tokens"] == 30


def test_times_detect_mixed_rounds_and_in_progress_stale_report(tmp_path):
    root = tmp_path / "mixed"
    archive(root / "one", [("a", 10, {})])
    archive(root / "two", [("a", 20, {})])
    with pytest.raises(ValueError, match="出现多次"):
        timing.load_archive(root)
    state_path = root / "two/runs/a/run_state.json"
    state = json.loads(state_path.read_text())
    state["status"] = "running"
    state_path.write_text(json.dumps(state))
    got = timing.load_archive(root / "two")
    assert got["cases"] == {} and got["unfinished_or_missing"] == ["a"]


def test_times_cached_batch_not_used_as_throughput_comparison(tmp_path):
    new, old = tmp_path / "new", tmp_path / "old"
    archive(new, [("a", 1, {})], window=10, cached=True)
    archive(old, [("a", 2, {})], window=20)
    got = timing.compare(new, old, tmp_path / "out")
    assert got["identical_completed_case_sets"]
    assert got["batch_recorded_window_change_fraction"] is None


def test_merge_segments_milliseconds_and_old_records_remain_missing(tmp_path):
    new, old = tmp_path / "new", tmp_path / "old"
    archive(new, [("a", 1, {})])
    archive(old, [("a", 2, {})])
    file = new / "runs/a/10_run_report.json"
    report = json.loads(file.read_text())
    report["merge_timing"] = {"queued_at": "2026-11-12T10:00:00.000+08:00",
        "started_at": "2026-11-12T10:00:00.123+08:00", "returned_at": "2026-11-12T10:00:00.789+08:00",
        "resumed_at": "2026-11-12T10:00:00.999+08:00"}
    file.write_text(json.dumps(report))
    out = tmp_path / "out"
    result = timing.compare(new, old, out)
    assert result["current"]["cases"]["a"]["merge_intervals_s"] == {
        "queue_s": .123, "execution_s": .666, "resume_wait_s": .210}
    assert all(v is None for v in result["baseline"]["cases"]["a"]["merge_intervals_s"].values())
    with (out / "merge_timing.csv").open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 3 and all(row["baseline_s"] == "" and row["delta_s"] == "" for row in rows)
