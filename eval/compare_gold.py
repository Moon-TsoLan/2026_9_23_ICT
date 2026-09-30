"""Compare final projects.json against eval/gold.

口径（见 doc/提示词编写规范.md 与讨论）：命中(召回) + 字段填充 + 多出。
不做字段值比较。只统计 run_state.status 已结束（success/partial/failed）的 run；
中断或残留的 projects.json 一律跳过。
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "eval" / "gold"
RUNS = ROOT / "work" / "runs"
OUT_JSON = ROOT / "eval" / "gold_report.json"
OUT_MD = ROOT / "eval" / "gold_report.md"

OFFICIAL = ("object_name", "category_name", "brand", "spec_model", "unit_price", "quantity", "total_price")
FINISHED = {"success", "partial", "failed"}


def norm(value) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    return re.sub(r"\s+", "", text)


def run_status(aid: str) -> str | None:
    path = RUNS / aid / "run_state.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("status")
    except Exception:
        return None


def load_projects(aid: str) -> list[dict] | None:
    if run_status(aid) not in FINISHED:
        return None
    path = RUNS / aid / "projects.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def rows_by_package(projects: list[dict]) -> dict[str, dict]:
    return {str(p.get("package_no")): p for p in projects}


def main() -> None:
    per: list[dict] = []
    total: Counter = Counter()
    field_filled: Counter = Counter()
    field_base = 0

    for gold_path in sorted(GOLD.glob("*.json")):
        gold = json.loads(gold_path.read_text(encoding="utf-8"))
        aid = gold["announcement_id"]
        g = rows_by_package(gold.get("projects", []))
        out = load_projects(aid)

        row = {"announcement_id": aid, "gold_packages": len(g), "status": run_status(aid)}
        if out is None:
            row["note"] = "跳过：run 未正常结束或缺少 projects.json"
            per.append(row)
            continue
        o = rows_by_package(out)

        row["out_packages"] = len(o)
        row["package_set_ok"] = set(g) == set(o)

        g_amt = {k: p.get("package_total_amount") for k, p in g.items()}
        o_amt = {k: p.get("package_total_amount") for k, p in o.items()}
        common = set(g_amt) & set(o_amt)
        amount_ok = sum(1 for k in common if g_amt[k] == o_amt[k])
        row["amount_ok"], row["amount_total"] = amount_ok, len(g_amt)
        total["amount_ok"] += amount_ok
        total["amount_total"] += len(g_amt)

        g_cobs = [c for p in g.values() for c in (p.get("cobs") or [])]
        o_cobs = [c for p in o.values() for c in (p.get("cobs") or [])]
        g_names = Counter(norm(c.get("object_name")) for c in g_cobs)
        o_names = Counter(norm(c.get("object_name")) for c in o_cobs)
        hit = sum((g_names & o_names).values())
        row.update(gold_cobs=len(g_cobs), out_cobs=len(o_cobs), cob_hit=hit, cob_extra=len(o_cobs) - hit)
        row["cob_recall"] = round(hit / len(g_cobs), 3) if g_cobs else None
        total["gold_cobs"] += len(g_cobs)
        total["cob_hit"] += hit
        total["out_cobs"] += len(o_cobs)

        g_subs = [s for p in g.values() for s in (p.get("subs") or [])]
        o_subs = [s for p in o.values() for s in (p.get("subs") or [])]
        g_sn = Counter(norm(s.get("supplier_name")) for s in g_subs)
        o_sn = Counter(norm(s.get("supplier_name")) for s in o_subs)
        sub_hit = sum((g_sn & o_sn).values())
        row.update(gold_subs=len(g_subs), out_subs=len(o_subs), sub_hit=sub_hit)
        row["sub_recall"] = round(sub_hit / len(g_subs), 3) if g_subs else None
        total["gold_subs"] += len(g_subs)
        total["sub_hit"] += sub_hit
        total["out_subs"] += len(o_subs)

        gold_name_set = set(g_names)
        matched = [c for c in o_cobs if norm(c.get("object_name")) in gold_name_set]
        fill = {}
        for f in OFFICIAL:
            filled = sum(1 for c in matched if c.get(f) not in (None, ""))
            fill[f] = round(filled / len(matched), 3) if matched else None
            field_filled[f] += filled
        field_base += len(matched)
        row["field_fill"] = fill
        per.append(row)

    overall = {
        "runs_compared": sum(1 for r in per if "cob_recall" in r),
        "cob_recall": round(total["cob_hit"] / total["gold_cobs"], 3) if total["gold_cobs"] else None,
        "cob_hit": total["cob_hit"],
        "gold_cobs": total["gold_cobs"],
        "out_cobs": total["out_cobs"],
        "cob_extra": total["out_cobs"] - total["cob_hit"],
        "sub_recall": round(total["sub_hit"] / total["gold_subs"], 3) if total["gold_subs"] else None,
        "sub_hit": total["sub_hit"],
        "gold_subs": total["gold_subs"],
        "package_amount_rate": round(total["amount_ok"] / total["amount_total"], 3) if total["amount_total"] else None,
        "field_fill": {f: (round(field_filled[f] / field_base, 3) if field_base else None) for f in OFFICIAL},
    }

    OUT_JSON.write_text(json.dumps({"overall": overall, "per": per}, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = ["# gold 对比报告", "", "口径：命中(召回) + 字段填充 + 多出；不做值比较。", ""]
    lines.append("## 总计")
    lines.append("")
    lines.append(f"- 标的命中率(召回)：{overall['cob_recall']}（{overall['cob_hit']}/{overall['gold_cobs']}），多出 {overall['cob_extra']}，输出 {overall['out_cobs']}")
    lines.append(f"- 供应商命中率：{overall['sub_recall']}（{overall['sub_hit']}/{overall['gold_subs']}）")
    lines.append(f"- 包金额一致率：{overall['package_amount_rate']}")
    lines.append(f"- 字段填充率(命中行)：{overall['field_fill']}")
    lines.append("")
    lines.append("## 逐公告")
    lines.append("")
    lines.append("| 公告 | 状态 | 标的命中 | 标的召回 | 多出 | 供应商命中 | 包金额 |")
    lines.append("|---|---|---|---:|---:|---|---|")
    for r in per:
        if "cob_recall" not in r:
            lines.append(f"| {r['announcement_id']} | {r.get('status')} | - | - | - | - | - |")
            continue
        lines.append(
            f"| {r['announcement_id']} | {r['status']} | {r['cob_hit']}/{r['gold_cobs']} | {r['cob_recall']} | "
            f"{r['cob_extra']} | {r['sub_hit']}/{r['gold_subs']} | {r['amount_ok']}/{r['amount_total']} |"
        )
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps(overall, ensure_ascii=False, indent=2))
    print(f"report -> {OUT_MD}")


if __name__ == "__main__":
    main()
