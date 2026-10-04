"""Load a batch of pipeline runs into a database built from db/001_schema.sql.

    python db/load_runs.py --db ict_batch20261004 --runs work/runs
    python db/load_runs.py --db ict_batch20261004 --runs work/runs --ids work/_step8_probe/heavy5.json

Reads each run directory (01 / 08 / 09 / 10) and fills announcement, project, cob,
supplier, bid, winner_coop_supplier. Structure and column meanings stay exactly as the
demo database defines them, so the frontend and db/queries.sql work unchanged. The demo
database is never touched: this writes only into the database named on the command line.

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
import re
import sys
from collections import Counter
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
    port = env_value("PGPORT", "5432")
    return f"postgresql://{user}:{password}@{host}:{port}/{database}"


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


def load_run(cur, run: Path) -> dict:
    read = lambda name: json.loads((run / name).read_text(encoding="utf-8")) if (run / name).exists() else None
    understanding = read("01_announcement_understanding.json") or {}
    merged = read("09_merged_projects.json") or {}
    report = read("10_run_report.json") or {}
    aid = (report.get("announcement_id") or understanding.get("run_id", "").replace("run_", "")
           or run.name)
    candidates = {c["candidate_id"]: c for c in (read("08_normalized_candidates.json") or {}).get("candidates") or []}
    summary = understanding.get("summary_amount") or {}
    audit = merged.get("amount_audit") or {}
    cur.execute(
        """INSERT INTO announcement (announcement_id, title, source_project_no, announcement_type,
                                      summary_amount_yuan, run_status, review_required, raw_json)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (announcement_id) DO UPDATE
             SET title = EXCLUDED.title, source_project_no = EXCLUDED.source_project_no,
                 announcement_type = EXCLUDED.announcement_type,
                 summary_amount_yuan = EXCLUDED.summary_amount_yuan,
                 run_status = EXCLUDED.run_status, review_required = EXCLUDED.review_required,
                 raw_json = EXCLUDED.raw_json""",
        (aid, understanding.get("project_name"), understanding.get("source_project_no"),
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
    for project in merged.get("projects") or []:
        prov = project.get("provenance") or {}
        base_ids = list(prov.get("cob_candidate_ids") or [])
        cur.execute(
            """INSERT INTO project (project_id, announcement_id, project_name, package_no, purchaser,
                                    package_total_amount, source_project_no)
               VALUES (%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (project_id) DO UPDATE
                 SET purchaser = EXCLUDED.purchaser, package_total_amount = EXCLUDED.package_total_amount""",
            (project["project_id"], aid, project.get("project_name"), str(project.get("package_no")),
             project.get("purchaser"), num(project.get("package_total_amount"), 2),
             project.get("source_project_no")))
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
                (project["project_id"], cob.get("object_name"), cob.get("category_code"),
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
                (project["project_id"], supplier_id, num(sub.get("score"), 3), bool(sub.get("is_winner"))))
            for coop in sub.get("cooperative_product_suppliers") or []:
                cur.execute(
                    """INSERT INTO winner_coop_supplier (project_id, supplier_id, product_supplier_name)
                       VALUES (%s,%s,%s) ON CONFLICT DO NOTHING""",
                    (project["project_id"], supplier_id, coop))
    projects = merged.get("projects") or []
    return {"aid": aid, "packages": len(projects),
            "cobs": sum(len(x.get("cobs") or []) for x in projects), "stats": stats}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--runs", default="work/runs")
    parser.add_argument("--ids", help="optional JSON array of announcement ids to load")
    parser.add_argument("--reset", action="store_true", help="truncate the target tables first")
    args = parser.parse_args()

    base = ROOT / args.runs
    wanted = None
    if args.ids:
        wanted = set(json.loads(Path(args.ids).read_text(encoding="utf-8")))
    targets = [d for d in sorted(base.iterdir())
               if d.is_dir() and (d / "09_merged_projects.json").exists()
               and (wanted is None or d.name in wanted)]
    print("loading %d runs into %s" % (len(targets), args.db))

    with psycopg.connect(dsn(args.db), autocommit=True) as conn:
        cur = conn.cursor()
        if args.reset:
            cur.execute("TRUNCATE winner_coop_supplier, bid, cob, supplier, project, announcement CASCADE")
        totals = Counter()
        source_mix = Counter()
        for run in targets:
            info = load_run(cur, run)
            totals["announcements"] += 1
            print("   %-22s 包=%-3d 标的=%d" % (run.name, info["packages"], info["cobs"]))
            for (kind, unpriced), n in info["stats"].items():
                source_mix[kind] += n
                if unpriced:
                    source_mix[kind + " (无价)"] += n
        counts = {table: cur.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                  for table in ("announcement", "project", "cob", "supplier", "bid", "winner_coop_supplier")}
    print("rows:", counts)
    print("标的来源类:", dict(source_mix.most_common()))


if __name__ == "__main__":
    main()
