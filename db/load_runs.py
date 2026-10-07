"""Load a batch of pipeline runs into a database built from db/001_schema.sql.

    python db/load_runs.py --db ict_batch20261004 --runs work/runs
    python db/load_runs.py --db ict_batch20261004 --runs work/runs --ids work/_step8_probe/heavy5.json

Reads each run directory (01 / 08 / 09 / 10) and fills announcement, project, cob,
supplier, bid, winner_coop_supplier. Structure and column meanings stay exactly as the
demo database defines them, so the frontend and db/queries.sql work unchanged. The demo
database is never touched: this writes only into the database named on the command line.

project 的唯一键是「项目编号 + 包号 + 轮次」：包号只在单则公告内唯一，重新采购时公告
会沿用或重排包号，所以同一 (项目编号, 包号) 被多则公告复用时用 round_no 区分
（project_id = <编号>|<包号> 或 <编号>|<包号>|rN）。轮次按公告号里的日期戳排序，
因为 announcement.created_at 是入库时间，不是公告发布日。

Column notes worth knowing when reading the UI:
- cob.source_type / source_file_id come from the candidate that became the object's base
  (provenance.cob_candidate_ids), so "来源文件类 = unknown" in the UI means the object came
  from a file the screen gate could not classify.
- announcement.raw_json carries the run report plus step-8 audit (merge decisions, amount
  audit, unmatched, conflicts) so a reviewer can open it without another table.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def env_value(key: str, default: str = "") -> str:
    env = ROOT / ".env"
    if env.exists():
        for raw in env.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line.startswith(key + "=") and "=" in line:
                return line.split("=", 1)[1].strip().strip("'").strip('"') or default
    return default


def dsn(database: str) -> str:
    user = env_value("PGUSER", "ict")
    password = env_value("PGPASSWORD", "ict_dev_pw")
    host = env_value("PGHOST", "localhost")
    # 15432 不是笔误：5432 落在本机的 Windows 动态保留区间里，主机绑不上。
    port = env_value("PGPORT", "15432")
    return f"postgresql://{user}:{password}@{host}:{port}/{database}"


def dsn_default() -> str:
    """工作进程用：DATABASE_URL 优先，否则 PG* + ICT_DB / PGDATABASE（默认 ict_demo）。"""
    url = (os.environ.get("DATABASE_URL") or env_value("DATABASE_URL")).strip()
    if url:
        return url
    return dsn(os.environ.get("ICT_DB") or env_value("PGDATABASE", "ict_demo"))


def num(value, decimals: int | None = None):
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, decimals) if decimals is not None else number


def char1(value):
    text = str(value or "").strip()
    return text[:1] if text[:1] in {"A", "B", "C"} else None


def _load_json(path: Path, default=None):
    if not path.exists():
        return default if default is not None else {}
    return json.loads(path.read_text(encoding="utf-8"))


def _announcement_id(run: Path, understanding: dict, report: dict) -> str:
    return (report.get("announcement_id") or understanding.get("run_id", "").replace("run_", "")
            or run.name)


def _date_key(aid: str) -> str:
    """公告号里的日期戳（tYYYYMMDD_…）；排序轮次用它，不用入库时间 created_at。"""
    match = re.match(r"t(\d{8})_", aid or "")
    return match.group(1) if match else "\uffff"


def _package_base_key(project: dict) -> str:
    """包的业务键：<项目编号>|<包号>；模型没抽到编号时退回项目名（与 002 迁移里的回填一致）。"""
    package_no = str(project.get("package_no"))
    number = project.get("source_project_no")
    return f"{number}|{package_no}" if number else f"{project.get('project_name') or '?'}|{package_no}"


def _desired_id(package_key: str, round_no: int) -> str:
    """第 1 轮不带后缀，第 2 轮起写成 `<package_key>|rN`。"""
    return package_key if round_no == 1 else f"{package_key}|r{round_no}"


def target_entries(targets: list[Path]) -> list[dict]:
    """规划轮次所需的全部信息：(公告号, 日期, 项目序号, 业务键)。"""
    entries: list[dict] = []
    for run in targets:
        merged = _load_json(run / "09_merged_projects.json")
        report = _load_json(run / "10_run_report.json")
        understanding = _load_json(run / "01_announcement_understanding.json")
        aid = _announcement_id(run, understanding, report)
        for index, project in enumerate(merged.get("projects") or []):
            entries.append({"aid": aid, "date": _date_key(aid), "index": index,
                            "package_key": _package_base_key(project)})
    return entries


@dataclass
class RoundPlan:
    """本次要写入的新行，以及已有行里需要改名/改轮次的那部分。"""

    inserts: dict[tuple[str, int], tuple[str, int, str]]   # (aid, 序号) -> (project_id, round_no, package_key)
    renames: list[tuple[str, str, int]]                    # (旧 project_id, 新 project_id, 新 round_no)


def plan_rounds(entries: list[dict], existing: list[tuple] = ()) -> RoundPlan:
    """按 (公告日期, 公告号) 给同一个 package_key 排轮次。

    `existing` 是库内已有行 `(package_key, announcement_id, project_id, round_no)`，
    **不含**本次要覆盖的那几则公告（它们会被删掉重建，不该参与排序）。
    新到的公告如果日期更早，已有行会被往后挤一位 —— 这件事靠 `renames` 表达出来。
    """
    rows: list[tuple] = []   # (package_key, date, aid, index|None, current_id|None)
    for entry in entries:
        rows.append((entry["package_key"], entry["date"], entry["aid"], entry["index"], None))
    for package_key, aid, project_id, _round in existing:
        rows.append((package_key, _date_key(aid), aid, None, project_id))

    grouped: dict[str, list[tuple]] = {}
    for row in rows:
        grouped.setdefault(row[0], []).append(row)

    inserts: dict[tuple[str, int], tuple[str, int, str]] = {}
    renames: list[tuple[str, str, int]] = []
    for package_key, group in grouped.items():
        group.sort(key=lambda row: (row[1], row[2]))
        for round_no, row in enumerate(group, start=1):
            desired = _desired_id(package_key, round_no)
            if row[3] is not None:                       # 本次新写入的行
                inserts[(row[2], row[3])] = (desired, round_no, package_key)
            elif row[4] != desired:                      # 已有行需要改名
                renames.append((row[4], desired, round_no))
    return RoundPlan(inserts, renames)


def fetch_existing(cur, package_keys: list[str], exclude_aids: list[str]) -> list[tuple]:
    """库内已有的同键行；排除本次要覆盖的公告。"""
    if not package_keys:
        return []
    cur.execute(
        """SELECT package_key, announcement_id, project_id, round_no
             FROM project
            WHERE package_key = ANY(%s) AND NOT (announcement_id = ANY(%s))""",
        (package_keys, exclude_aids),
    )
    return cur.fetchall()


def load_run(cur, run: Path, inserts: dict[tuple[str, int], tuple[str, int, str]],
             tenant: str = "default") -> dict:
    read = lambda name: json.loads((run / name).read_text(encoding="utf-8")) if (run / name).exists() else None
    understanding = read("01_announcement_understanding.json") or {}
    merged = read("09_merged_projects.json") or {}
    report = read("10_run_report.json") or {}
    aid = _announcement_id(run, understanding, report)
    candidates = {c["candidate_id"]: c for c in (read("08_normalized_candidates.json") or {}).get("candidates") or []}
    summary = understanding.get("summary_amount") or {}
    audit = merged.get("amount_audit") or {}
    cur.execute(
        """INSERT INTO announcement (announcement_id, tenant_id, title, source_project_no, announcement_type,
                                      summary_amount_yuan, run_status, review_required, raw_json)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (announcement_id) DO UPDATE
             SET title = EXCLUDED.title, source_project_no = EXCLUDED.source_project_no,
                 announcement_type = EXCLUDED.announcement_type,
                 summary_amount_yuan = EXCLUDED.summary_amount_yuan,
                 run_status = EXCLUDED.run_status, review_required = EXCLUDED.review_required,
                 raw_json = EXCLUDED.raw_json, tenant_id = EXCLUDED.tenant_id""",
        (aid, tenant, understanding.get("project_name"), understanding.get("source_project_no"),
         understanding.get("announcement_type"), num(summary.get("amount_yuan"), 2),
         report.get("status"), bool(report.get("review_required")),
         json.dumps({"report": {k: report.get(k) for k in
                                ("status", "counts", "llm_calls", "failure_summary", "duration_ms")},
                     "merge_decisions": merged.get("merge_decisions") or [],
                     "amount_audit": audit, "unmatched_summary_rows": merged.get("unmatched_summary_rows") or [],
                     "conflicts": merged.get("conflicts") or [], "checks": merged.get("checks") or [],
                     "repairs": merged.get("repairs") or [],
                     "package_amount_observations": {k: (v or {}).get("observations") for k, v in audit.items()}},
                    ensure_ascii=False)))
    stats = Counter()
    rounds = Counter()
    for seq, project in enumerate(merged.get("projects") or []):
        project_id, round_no, package_key = inserts.get(
            (aid, seq), (project["project_id"], 1, _package_base_key(project))
        )
        rounds[round_no] += 1
        prov = project.get("provenance") or {}
        base_ids = list(prov.get("cob_candidate_ids") or [])
        cur.execute(
            """INSERT INTO project (project_id, package_key, package_no, round_no,
                                    source_project_no, announcement_id, project_name,
                                    purchaser, package_total_amount)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (project_id, package_key, str(project.get("package_no")), round_no,
             project.get("source_project_no"), aid, project.get("project_name"),
             project.get("purchaser"), num(project.get("package_total_amount"), 2)))
        for index, cob in enumerate(project.get("cobs") or []):
            source = candidates.get(base_ids[index]) if index < len(base_ids) else None
            origin = ((source or {}).get("source") or {})
            kind = (source or {}).get("source_class") or ("html" if origin.get("source_type") == "html" else None)
            stats[(kind or origin.get("source_type") or "?",
                   cob.get("total_price") is None and cob.get("unit_price") is None)] += 1
            cur.execute(
                """INSERT INTO cob (project_id, object_name, category_code, category_name, category_type,
                                    brand, product_supplier, spec_model, unit_price, quantity, unit,
                                    total_price, source_type, source_file_id)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (project_id, cob.get("object_name"), cob.get("category_code"),
                 cob.get("category_name"), char1(cob.get("category_type")), cob.get("brand"),
                 cob.get("product_supplier"), cob.get("spec_model"), num(cob.get("unit_price"), 2),
                 num(cob.get("quantity"), 4), cob.get("unit"), num(cob.get("total_price"), 2),
                 {"vl": "pdf"}.get(origin.get("source_type"), origin.get("source_type")),
                 origin.get("file_id")))
        for sub in project.get("subs") or []:
            name = sub.get("supplier_name")
            if not name:
                continue
            row = cur.execute(
                """INSERT INTO supplier (name, norm_name) VALUES (%s, norm_name(%s))
                   ON CONFLICT (norm_name) DO UPDATE SET name = supplier.name
                   RETURNING supplier_id""", (name, name)).fetchone()
            supplier_id = row[0]
            cur.execute(
                """INSERT INTO bid (project_id, supplier_id, score, is_winner)
                   VALUES (%s,%s,%s,%s) ON CONFLICT (project_id, supplier_id)
                   DO UPDATE SET score = COALESCE(EXCLUDED.score, bid.score),
                                 is_winner = bid.is_winner OR EXCLUDED.is_winner""",
                (project_id, supplier_id, num(sub.get("score"), 3), bool(sub.get("is_winner"))))
            for coop in sub.get("cooperative_product_suppliers") or []:
                cur.execute(
                    """INSERT INTO winner_coop_supplier (project_id, supplier_id, product_supplier_name)
                       VALUES (%s,%s,%s) ON CONFLICT DO NOTHING""",
                    (project_id, supplier_id, coop))
    projects = merged.get("projects") or []
    return {"aid": aid, "packages": len(projects), "rounds": rounds,
            "cobs": sum(len(x.get("cobs") or []) for x in projects), "stats": stats}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--runs", default="work/runs")
    parser.add_argument("--ids", help="optional JSON array of announcement ids to load")
    parser.add_argument("--reset", action="store_true", help="truncate the target tables first")
    parser.add_argument("--tenant", default="default", help="租户/账号，现在恒为 default")
    args = parser.parse_args()

    base = ROOT / args.runs
    wanted = None
    if args.ids:
        wanted = set(json.loads(Path(args.ids).read_text(encoding="utf-8")))
    targets = [d for d in sorted(base.iterdir())
               if d.is_dir() and (d / "09_merged_projects.json").exists()
               and (wanted is None or d.name in wanted)]
    print("loading %d runs into %s" % (len(targets), args.db))
    # 一个事务：覆盖 + 轮次重排必须一起成败，否则中途失败会留下"停到临时 id"的行。
    with psycopg.connect(dsn(args.db)) as conn:
        cur = conn.cursor()
        if args.reset:
            cur.execute("TRUNCATE winner_coop_supplier, bid, cob, supplier, project, announcement CASCADE")
        info = load_batch(cur, targets, args.tenant)
        counts = {table: cur.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                  for table in ("announcement", "project", "cob", "supplier", "bid", "winner_coop_supplier")}
    _print_summary(info, counts)


def load_batch(cur, targets: list[Path], tenant: str = "default") -> dict:
    """把一批 run 写进库：覆盖式入库 + 轮次重排。

    调用方负责事务边界（成功提交、失败回滚）。工作进程每则调一次也用这个函数，
    所以批量与实时的写库行为完全一致。
    """
    entries = target_entries(targets)
    package_keys = sorted({entry["package_key"] for entry in entries})
    replace_aids = sorted({entry["aid"] for entry in entries})
    existing = fetch_existing(cur, package_keys, replace_aids)
    plan = plan_rounds(entries, existing)

    # ① 覆盖式入库：先删同号公告（级联删 project 与 cob/bid/winner_coop_supplier）
    if replace_aids:
        cur.execute("DELETE FROM announcement WHERE announcement_id = ANY(%s)", (replace_aids,))
    # ② 要改名的已有行先停到临时 id + 负轮次：
    #    两阶段更新，交换时既不撞 (package_key, round_no)，也不撞主键。
    for old, _new, _round in plan.renames:
        cur.execute("UPDATE project SET project_id = %s, round_no = -round_no WHERE project_id = %s",
                    (old + "~park", old))

    totals: Counter = Counter()
    source_mix = Counter()
    round_mix = Counter()
    per_run: list[dict] = []
    for run in targets:
        info = load_run(cur, run, plan.inserts, tenant)
        totals["announcements"] += 1
        per_run.append(info)
        round_mix.update(info["rounds"])
        for (kind, unpriced), n in info["stats"].items():
            source_mix[kind] += n
            if unpriced:
                source_mix[kind + " (无价)"] += n

    # ③ 已有行落到目标 id 与轮次；子表靠 ON UPDATE CASCADE 自动跟随
    for old, new, round_no in plan.renames:
        cur.execute("UPDATE project SET project_id = %s, round_no = %s WHERE project_id = %s",
                    (new, round_no, old + "~park"))
    # ④ 清掉不再被任何投标引用的主体（覆盖式入库会留下它们）
    orphans = cur.execute(
        "DELETE FROM supplier s WHERE NOT EXISTS (SELECT 1 FROM bid b WHERE b.supplier_id = s.supplier_id)"
    ).rowcount
    return {"plan": plan, "per_run": per_run, "totals": totals, "round_mix": round_mix,
            "source_mix": source_mix, "orphans": orphans, "replace_aids": replace_aids}


def _print_summary(info: dict, counts: dict) -> None:
    plan = info["plan"]
    multi_round = sum(1 for _, (_, round_no, _) in plan.inserts.items() if round_no > 1)
    base_keys: Counter = Counter()
    for _, (_, _, base) in plan.inserts.items():
        base_keys[base] += 1
    repeated = {base: n for base, n in base_keys.items() if n > 1}
    if repeated:
        print("同 (项目编号,包号) 出现在多则公告的业务键 %d 个，共 %d 个包行："
              % (len(repeated), multi_round))
        for base, n in sorted(repeated.items()):
            print("   %s  ×%d" % (base, n))
    if plan.renames:
        print("库内已有 %d 个包行要按公告日期重排：" % len(plan.renames))
        for old, new, round_no in plan.renames:
            print("   %s -> %s（第 %d 轮）" % (old, new, round_no))
    for run in info["per_run"]:
        print("   %-22s 包=%-3d 标的=%d" % (run["aid"], run["packages"], run["cobs"]))
    print("rows:", counts)
    if info["orphans"]:
        print("清掉孤儿主体:", info["orphans"])
    print("包行按轮次分布:", dict(sorted(info["round_mix"].items())))
    print("标的来源类:", dict(info["source_mix"].most_common()))


if __name__ == "__main__":
    main()
