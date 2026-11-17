"""离线比对运行目录中的处理耗时：python compare_times.py 新运行目录 [旧运行目录]。"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def stats(values):
    values = sorted(values)
    if not values:
        return {key: None for key in ("count", "sum_s", "mean_s", "median_s", "p90_s", "max_s")}
    position = (len(values) - 1) * 0.9
    lo, hi = math.floor(position), math.ceil(position)
    return {"count": len(values), "sum_s": sum(values), "mean_s": statistics.mean(values),
            "median_s": statistics.median(values), "p90_s": values[lo] + (values[hi] - values[lo]) * (position - lo),
            "max_s": max(values)}


MERGE_INTERVALS = {"queue_s": ("queued_at", "started_at"),
                   "execution_s": ("started_at", "returned_at"),
                   "resume_wait_s": ("returned_at", "resumed_at")}


def merge_intervals(timing):
    values = {}
    for name, (start, end) in MERGE_INTERVALS.items():
        a, b = (timing or {}).get(start), (timing or {}).get(end)
        value = (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() if a and b else None
        if value is not None and value < 0:
            raise ValueError(f"合并时间倒序：{start} / {end}")
        values[name] = value
    return values


def load_archive(path):
    path = Path(path).resolve()
    run_root = path / "runs" if (path / "runs").is_dir() else path
    archive = run_root.parent if run_root.name == "runs" else path
    files = [run_root] if run_root.is_file() and run_root.name == "10_run_report.json" else sorted(run_root.rglob("10_run_report.json"))
    if not files:
        raise ValueError(f"没有找到10_run_report.json：{path}。请提供单轮测试目录、runs目录或单则运行目录")
    cases, starts, ends, unfinished, seen = {}, [], [], set(), set()
    for file in files:
        report = read(file)
        aid = report["announcement_id"]
        if aid in seen:
            raise ValueError(f"目录内UUID {aid}出现多次；请分别选择单个账号、单轮实验目录，避免混合记录")
        seen.add(aid)
        state_path = file.with_name("run_state.json")
        state = read(state_path) if state_path.exists() else {}
        if state and state["announcement_id"] != aid:
            raise ValueError(f"报告与进度的公告ID不一致：{file}")
        if state.get("status") == "running":
            unfinished.add(aid)  # a rerun may still have the old completed report beside it
            continue
        if state.get("status") and state["status"] != report["status"]:
            raise ValueError(f"报告与进度状态不一致，可能混入重跑前的旧报告：{file}")
        duration = report["duration_ms"]
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration < 0:
            raise ValueError(f"duration_ms必须是非负有限数字：{file}")
        stages, open_steps = {}, []
        for step in state.get("steps", []):
            if not step.get("started_at") or not step.get("ended_at"):
                open_steps.append(step["step"])
                continue
            start, end = datetime.fromisoformat(step["started_at"]), datetime.fromisoformat(step["ended_at"])
            if end < start or step["step"] in stages:
                raise ValueError(f"步骤时间倒序或重复：{file} / {step['step']}")
            stages[step["step"]] = (end - start).total_seconds()
            ends.append(end)
        if state.get("created_at"):
            starts.append(datetime.fromisoformat(state["created_at"]))
        calls = report.get("model_calls", [])
        by_step = defaultdict(list)
        usage = Counter()
        for call in calls:
            if call.get("latency_ms") is not None:
                by_step[call["step"]].append(call["latency_ms"] / 1000)
            usage.update({k: v for k, v in (call.get("usage") or {}).items() if isinstance(v, (int, float))})
        cases[aid] = {"UUID": aid, "status": report["status"], "total_s": duration / 1000,
                      "merge_intervals_s": merge_intervals(report.get("merge_timing") or state.get("merge_timing")),
                      "stages_s": stages, "open_steps": open_steps, "missing_state": not bool(state),
                      "calls": len(calls), "calls_by_step": dict(by_step), "usage": dict(usage),
                      "report_path": str(file)}
    if run_root.is_dir():
        for state_path in run_root.rglob("run_state.json"):
            if not state_path.with_name("10_run_report.json").exists():
                unfinished.add(read(state_path)["announcement_id"])
    manifest = read(archive / "manifest.json") if (archive / "manifest.json").exists() else {}
    integrity = read(archive / "integrity.json") if (archive / "integrity.json").exists() else {}
    recorded_window = None
    if manifest.get("start_time") and integrity.get("end_time"):
        recorded_window = (datetime.fromisoformat(integrity["end_time"]) - datetime.fromisoformat(manifest["start_time"])).total_seconds()
        if recorded_window < 0:
            raise ValueError("manifest和integrity的时间先后矛盾")
    active_window = (max(ends) - min(starts)).total_seconds() if ends and starts else None
    expected = set(manifest.get("ids", []))
    unfinished.update(expected - set(cases))
    cached = sum(bool(r.get("cached_completed")) for r in read(archive / "progress.json")) if (archive / "progress.json").exists() else 0
    return {"path": str(path), "cases": cases, "unfinished_or_missing": sorted(unfinished),
            "statuses": dict(Counter(row["status"] for row in cases.values())),
            "case_latency": stats([row["total_s"] for row in cases.values()]),
            "recorded_batch_window_s": recorded_window, "observed_active_span_s": active_window,
            "cached_completed": cached, "concurrency": manifest.get("concurrency"),
            "scheduling": manifest.get("scheduling"), "manifest_ids": sorted(expected)}


def write_csv(path, rows):
    if not rows:
        return
    with Path(path).open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def number(value):
    return "未记录" if value is None else f"{value:.3f}"


def _change(old, new):
    return None if old in (None, 0) or new is None else new / old - 1


def compare(current_path, baseline_path, out):
    current = load_archive(current_path)
    baseline = load_archive(baseline_path) if baseline_path else None
    ids = sorted(set(current["cases"]) & set(baseline["cases"])) if baseline else sorted(current["cases"])
    if not ids:
        raise ValueError("没有双方共有且已完成的案例可比")
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    case_rows, stage_rows, call_rows, merge_rows = [], [], [], []
    for aid in ids:
        new = current["cases"][aid]
        old = baseline["cases"][aid] if baseline else None
        case_rows.append({"UUID": aid, "current_status": new["status"], "current_s": new["total_s"],
                          "baseline_status": old["status"] if old else None,
                          "baseline_s": old["total_s"] if old else None,
                          "delta_s": new["total_s"] - old["total_s"] if old else None,
                          "change_fraction": _change(old["total_s"], new["total_s"]) if old else None})
        for segment in MERGE_INTERVALS:
            ns = new["merge_intervals_s"][segment]
            os = old["merge_intervals_s"][segment] if old else None
            merge_rows.append({"UUID": aid, "segment": segment, "current_s": ns, "baseline_s": os,
                               "delta_s": ns - os if ns is not None and os is not None else None})
        for step in sorted(set(new["stages_s"]) | (set(old["stages_s"]) if old else set())):
            ns, os = new["stages_s"].get(step), old["stages_s"].get(step) if old else None
            stage_rows.append({"UUID": aid, "step": step, "current_s": ns, "baseline_s": os,
                               "delta_s": ns - os if ns is not None and os is not None else None})
        for group, row in (("current", new), ("baseline", old)):
            if row:
                for step, values in row["calls_by_step"].items():
                    call_rows.append({"group": group, "UUID": aid, "step": step, **stats(values)})
    current_common = stats([r["current_s"] for r in case_rows])
    baseline_common = stats([r["baseline_s"] for r in case_rows]) if baseline else None
    same_set = bool(baseline and set(current["cases"]) == set(baseline["cases"]))
    full_comparable = same_set and not current["unfinished_or_missing"] and not baseline["unfinished_or_missing"] and not current["cached_completed"] and not baseline["cached_completed"]
    summary = {"current": current, "baseline": baseline, "common_ids": ids,
               "current_common_latency": current_common, "baseline_common_latency": baseline_common,
               "identical_completed_case_sets": same_set,
               "batch_recorded_window_change_fraction": _change(baseline["recorded_batch_window_s"], current["recorded_batch_window_s"]) if full_comparable else None}
    (out / "timing.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(out / "cases.csv", case_rows)
    write_csv(out / "stages.csv", stage_rows)
    write_csv(out / "model_calls.csv", call_rows)
    write_csv(out / "merge_timing.csv", merge_rows)
    lines = ["# 处理时间比对", "", f"当前：{current['path']}",
             f"基线：{baseline['path'] if baseline else '未提供'}", f"共有已完成案例：{len(ids)}。", "",
             "| 指标 | 基线 | 当前 |", "|---|---:|---:|"]
    for key, label in (("mean_s", "单则平均秒数"), ("median_s", "单则中位秒数"), ("p90_s", "单则P90秒数"),
                       ("max_s", "单则最长秒数"), ("sum_s", "案例耗时合计（含并发重叠）")):
        lines.append(f"| {label} | {number(baseline_common[key]) if baseline_common else '未提供'} | {number(current_common[key])} |")
    for key, label in (("recorded_batch_window_s", "整批记录首尾窗口（秒）"), ("observed_active_span_s", "已完成案例起止跨度（秒）")):
        lines.append(f"| {label} | {number(baseline[key]) if baseline else '未提供'} | {number(current[key])} |")
    lines += ["", "| 合并分段 | 基线有记录案例数 | 基线均秒 | 当前有记录案例数 | 当前均秒 |",
              "|---|---:|---:|---:|---:|"]
    for segment, label in (("queue_s", "等待合并池"), ("execution_s", "合并函数执行（含内部任务排队）"),
                           ("resume_wait_s", "返回后等待接续")):
        ns = [r["current_s"] for r in merge_rows if r["segment"] == segment and r["current_s"] is not None]
        os = [r["baseline_s"] for r in merge_rows if r["segment"] == segment and r["baseline_s"] is not None]
        lines.append(f"| {label} | {len(os)} | {number(statistics.mean(os) if os else None)} | {len(ns)} | {number(statistics.mean(ns) if ns else None)} |")
    lines += ["", "| 阶段 | 可比案例数 | 基线均秒 | 当前均秒 | 平均变化秒 |", "|---|---:|---:|---:|---:|"]
    for step in sorted({r["step"] for r in stage_rows}):
        part = [r for r in stage_rows if r["step"] == step and r["current_s"] is not None
                and (not baseline or r["baseline_s"] is not None)]
        if not part:
            continue
        ns = statistics.mean(r["current_s"] for r in part)
        os = statistics.mean(r["baseline_s"] for r in part) if baseline else None
        lines.append(f"| {step} | {len(part)} | {number(os)} | {number(ns)} | {number(ns - os) if os is not None else '未提供'} |")
    lines += ["", f"当前状态：{current['statuses']}；未完成/缺失：{current['unfinished_or_missing']}。",
              f"基线状态：{baseline['statuses'] if baseline else '未提供'}；未完成/缺失：{baseline['unfinished_or_missing'] if baseline else '未提供'}。",
              f"仅当前有记录：{sorted(set(current['cases']) - set(ids))}。",
              f"仅基线有记录：{sorted(set(baseline['cases']) - set(ids)) if baseline else []}。", "",
              "时间口径：",
              "- 单则总耗时来自10_run_report.json.duration_ms，毫秒。包括OCR/API/合并排队及接续等待，排除首次取得案例名额前等待、解压、数据库入库和原件清理。",
              "- 步骤时间来自run_state.json的ISO 8601起止时间（含+08:00），为秒精度。新增merge_timing为毫秒精度，分拆合并排队、函数执行、接续等待；旧记录缺少时保持未记录。",
              "- 整批记录窗口来自manifest.start_time到integrity.end_time；它可能包含准备、中断、恢复与复用旧成品，不能仅凭此证明连续运行速度。",
              "- 已完成案例起止跨度由进度记录计算，是并发活动跨度，不是案例耗时之和；缺少或未完成案例时不能代表整批。",
              "- 模型调用延迟含API闸门等待及可能的重试；多个调用会重叠，model_calls.csv的sum_s不等于阶段墙钟时间。",
              "- 比较前需确认两轮处理范围一致；完整流程与只接续合并后半程的实验不能直接比较整批速度。",
              "- P90使用线性插值；缺失步骤保持空白，不补零。", "",
              f"整批记录比较条件（相同完成集合、无已知缺失或复用成品）：{full_comparable}。",
              f"当前复用成品数：{current['cached_completed']}；基线：{baseline['cached_completed'] if baseline else '未提供'}。"]
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("current", type=Path, help="新实验目录、runs目录或单则目录")
    parser.add_argument("baseline", nargs="?", type=Path, help="旧实验目录、runs目录或单则目录")
    parser.add_argument("--out", type=Path, help="新输出目录")
    args = parser.parse_args()
    parent = args.current.parent if args.current.is_file() else args.current
    out = args.out or parent / ("timing_comparison_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    try:
        result = compare(args.current, args.baseline, out)
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(1, f"耗时比对失败：{exc}\n")
    values = result["current_common_latency"]
    print(f"可比{len(result['common_ids'])}则；当前平均{values['mean_s']:.3f}秒，中位{values['median_s']:.3f}秒")
    print(f"完整报告：{out.resolve() / 'report.md'}")


if __name__ == "__main__":
    main()
