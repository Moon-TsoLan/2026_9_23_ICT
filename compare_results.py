"""比对网页三表导出与金标。用法：python compare_results.py "下载文件或CSV目录路径"。"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from ict.official import HEADERS, OPTIONAL, read_gold_workbook
from eval.performance.evaluate import score, score_sheets, load_score, diff

DEFAULT_GOLD = ROOT / "data/50条标注_官方三表_报价补全.xlsx"
DEFAULT_BASELINE = ROOT / "work/real20-evidence-trim-20261110/runs"


def blue_headers(name):
    return [h for h in HEADERS[name] if h not in OPTIONAL and not (name == "标的表" and h == "类别")]


def gold_snapshot(path):
    """Adapt the existing workbook reader to the existing evaluator's snapshot shape."""
    sheets = {}
    for name, rows in read_gold_workbook(path).items():
        records = [{**row, "filled_cells": {get_column_letter(i + 1): True
                    for i, field in enumerate(HEADERS[name]) if row["cells"].get(field, {}).get("inferred")}}
                   for row in rows]
        sheets[name] = {"headers": HEADERS[name], "blue_headers": blue_headers(name), "rows": records}
    return {"sheets": sheets, "ids": sorted({r["values"]["UUID"] for rows in sheets.values() for r in rows["rows"]})}


def _table(headers):
    matches = [name for name in HEADERS if set(blue_headers(name)) <= set(headers)]
    if len(matches) != 1:
        raise ValueError("无法根据表头识别三表之一，请保留官方蓝色字段表头：" + ", ".join(headers))
    return matches[0]


def _records(headers, rows):
    if len(headers) != len(set(headers)):
        raise ValueError("导出文件存在重复表头")
    records = []
    for number, row in enumerate(rows, 2):
        if not any(value not in (None, "") for value in row):
            continue
        if len(row) > len(headers) and any(value not in (None, "") for value in row[len(headers):]):
            raise ValueError(f"第{number}行的数据列数超过表头，请检查CSV引号和分隔符")
        values = {key: row[i] if i < len(row) else None for i, key in enumerate(headers) if key}
        aid = str(values.get("UUID") or "").strip()
        if not aid:
            raise ValueError(f"第{number}行缺少UUID，无法归入测试案例")
        values["UUID"] = aid
        records.append({"row": number, "values": values})
    return records


def read_export(path):
    """A three-sheet XLSX, one official CSV, or a directory containing one CSV per table."""
    path = Path(path)
    if path.is_dir():
        files = sorted(path.glob("*.csv"))
        if not files:
            raise ValueError(f"目录内没有CSV文件：{path}")
        sheets, columns = {}, {}
        for file in files:
            part, cols = read_export(file)
            if set(sheets) & set(part):
                raise ValueError("目录中同一张表出现多次，请每张表只保留一份CSV")
            sheets.update(part)
            columns.update(cols)
        return sheets, columns
    if path.suffix.lower() == ".xlsx":
        book = load_workbook(path, read_only=True, data_only=True)
        try:
            sheets, columns = {}, {}
            for name in HEADERS:
                if name not in book.sheetnames:
                    continue
                rows = book[name].iter_rows(values_only=True)
                headers = [str(v).strip() if v is not None else "" for v in next(rows, ())]
                if _table(headers) != name:
                    raise ValueError(f"{name}的表头与表名不对应")
                sheets[name], columns[name] = _records(headers, rows), headers
            if not sheets:
                raise ValueError("工作簿没有官方三表")
            return sheets, columns
        finally:
            book.close()
    if path.suffix.lower() != ".csv":
        raise ValueError("请提供网页下载的.xlsx、单张.csv，或三张CSV所在目录")
    try:
        content = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        content = path.read_text(encoding="gb18030")
    try:
        dialect = csv.Sniffer().sniff(content[:65536], delimiters=",\t;")
    except csv.Error:
        dialect = csv.excel
    rows = csv.reader(content.splitlines(keepends=True), dialect)
    headers = [v.strip() for v in next(rows, ())]
    name = _table(headers)
    return {name: _records(headers, rows)}, {name: headers}


def evaluate_export(sheets, columns, gold, ids=None):
    present = {row["values"]["UUID"] for rows in sheets.values() for row in rows}
    ids = sorted(set(ids if ids is not None else present) & set(gold["ids"]))
    if not ids:
        raise ValueError("导出文件中没有与金标重合的UUID")
    subset = {"sheets": {name: gold["sheets"][name] for name in sheets}}
    selected = {name: [r for r in rows if r["values"]["UUID"] in ids] for name, rows in sheets.items()}
    return score_sheets(selected, ids, subset, columns=columns), ids, sorted(present - set(gold["ids"]))


def _percent(value):
    return "未计算" if value is None else f"{value:.1%}"


def _rate(value):
    n, d = value.get("numerator", value.get("n")), value.get("denominator", value.get("d"))
    return f"{n}/{d}（{_percent(value['rate'])}）"


def compare(path, out, gold_path=DEFAULT_GOLD, baseline=DEFAULT_BASELINE):
    sheets, columns = read_export(path)
    gold = gold_snapshot(gold_path)
    current, ids, unknown = evaluate_export(sheets, columns, gold)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    scorer = load_score()
    scorer.dump(out / "metrics.json", current)
    scorer.write_csv(out / "blue_cells.csv", current["cells"])
    scorer.write_csv(out / "gray_cells.csv", current["gray_cells"])
    scorer.write_csv(out / "differences.csv", [r for r in current["cells"] + current["gray_cells"]
                                               if r["status"] not in {"correct_empty", "correct_nonempty", "allowed_empty_inferred"}])
    fields = [{"table_field": field, **{k: v for k, v in metrics.items() if not isinstance(v, dict)},
               "accuracy": metrics["accuracy_gold_slots"]["rate"],
               "precision": metrics["precision_including_extra_rows"]["rate"],
               "recall": metrics["correct_nonempty_recall"]["rate"]}
              for field, metrics in current["by_field"].items()]
    scorer.write_csv(out / "field_metrics.csv", fields)
    lines = ["# 导出结果比对", "", f"输入：{Path(path).resolve()}",
             f"金标：{Path(gold_path).resolve()}", f"评分案例：{len(ids)}则；评分表：{'、'.join(sheets)}。", "",
             "| 表 | 金标行 | 结果行 | 漏行 | 多抽 | 蓝色正确率 | 正确非空召回 | 行覆盖率 |",
             "|---|---:|---:|---:|---:|---|---|---|"]
    for name, metrics in current["metrics"].items():
        a = current["alignment"][name]
        lines.append(f"| {name} | {a['gold_rows']} | {a['result_rows']} | {a['missing_rows']} | {a['extra_rows']} | "
                     f"{_rate(metrics['accuracy_gold_slots'])} | {_rate(metrics['correct_nonempty_recall'])} | {_rate(metrics['row_coverage'])} |")
    lines += ["", "| 表 | 灰色正确率 | 含多抽的非空精确率（蓝色） | 已填覆盖率（蓝色） |",
              "|---|---|---|---|"]
    for name, metrics in current["metrics"].items():
        lines.append(f"| {name} | {_rate(current['gray_metrics'][name]['accuracy_gold_slots'])} | "
                     f"{_rate(metrics['precision_including_extra_rows'])} | {_rate(metrics['filled_coverage'])} |")
    regression = None
    if baseline and Path(baseline).exists():
        baseline = Path(baseline)
        if baseline.is_dir() and any(baseline.glob("*/projects.json")):
            common_ids = [aid for aid in ids if (baseline / aid / "projects.json").exists()]
            old = score(baseline, common_ids, {"sheets": {n: gold["sheets"][n] for n in sheets}}) if common_ids else None
        else:
            bs, bc = read_export(baseline)
            common_ids = sorted(set(ids) & {r["values"]["UUID"] for rows in bs.values() for r in rows})
            shared = set(sheets) & set(bs)
            old = evaluate_export({n: bs[n] for n in shared}, {n: bc[n] for n in shared}, gold, common_ids)[0] if shared and common_ids else None
        if old is not None:
            same = evaluate_export({n: sheets[n] for n in old["metrics"]}, columns, gold, common_ids)[0]
            gray_diff = diff({**old, "cells": old["gray_cells"]}, {**same, "cells": same["gray_cells"]})
            regression = {"baseline": str(baseline.resolve()), "ids": common_ids, **diff(old, same),
                          "gray_counts": gray_diff["counts"], "gray_cells": gray_diff["cells"]}
            scorer.dump(out / "regression.json", regression)
            scorer.write_csv(out / "regression.csv", [{"field_group": "blue", **r} for r in regression["cells"]]
                             + [{"field_group": "gray", **r} for r in gray_diff["cells"]])
            lines += ["", f"回归基线：{baseline.resolve()}。仅比较双方共有的{len(common_ids)}则及共有表。",
                      f"蓝色格变化：{regression['counts']}；灰色格变化：{gray_diff['counts']}。逐格明细见regression.csv（有变化时生成）。"]
    if regression is None:
        lines += ["", "回归：未找到可比较基线。可用--baseline指定另一份导出或历史runs目录。"]
    lines += ["", "评分范围与记录边界：",
              "- 正确率分母是这些案例在金标中的全部字段格；漏行的字段仍计入。CSV没有提供的其他表不评分。",
              "- 金标有填色的格沿用允许空值政策；蓝色、灰色分列。缺少的灰色列不评分。",
              "- 未出现在任何导出行中的整则失败案例无法从导出识别，未计入本次范围。",
              f"- 不在金标中的UUID（未评分）：{unknown}。",
              "- 耗时、token、实际费用及目检/OCR页覆盖无法从三表导出还原，需要对应运行记录；本报告不填造这些指标。"]
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    scorer.dump(out / "inputs.json", {"input": str(Path(path).resolve()), "gold": str(gold_path),
        "gold_sha256": hashlib.sha256(Path(gold_path).read_bytes()).hexdigest(), "ids": ids,
        "tables": list(sheets), "columns": columns, "unknown_ids": unknown,
        "baseline": str(baseline) if regression else None})
    return current


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", type=Path, help="网页导出.xlsx、单张.csv或三张CSV的目录")
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--out", type=Path, help="新输出目录（默认在输入旁边新建）")
    args = parser.parse_args()
    path = args.path or Path(input("请输入网页导出文件或CSV目录路径：").strip().strip('"'))
    parent = path.parent if path.is_file() else path
    out = args.out or parent / ("comparison_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    try:
        result = compare(path, out, args.gold, args.baseline)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"比对失败：{exc}\n")
    for name, metrics in result["metrics"].items():
        a = result["alignment"][name]
        print(f"{name}：正确率{_rate(metrics['accuracy_gold_slots'])}；漏行{a['missing_rows']}；多抽{a['extra_rows']}")
    print(f"完整报告：{out.resolve() / 'report.md'}")


if __name__ == "__main__":
    main()
