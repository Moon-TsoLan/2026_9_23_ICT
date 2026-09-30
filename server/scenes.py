"""五大业务场景（S1–S5）与星图总览的查询与结果组装。

每个场景返回统一的 SceneResult 形状：
  { scene, elapsed_ms, graph: {nodes, edges, highlight_ids}, narrative: {...} }
图节点 id 约定（前端视为不透明字符串）：
  buyer:<采购单位名>  proj:<project_id>  sup:<主体名>  vendor:<产品供应商名>
"""
from __future__ import annotations

from time import perf_counter
from typing import Any

from .db import num, q, q1, yuan

Node = dict[str, Any]
Edge = dict[str, Any]


def _node(id: str, label: str, kind: str, weight: Any = 0) -> Node:
    return {"id": id, "label": label, "kind": kind, "weight": num(weight) or 0}


def _edge(a: str, b: str, role: str, weight: Any = 1) -> Edge:
    return {"a": a, "b": b, "role": role, "weight": num(weight) or 1}


def _projects_of_purchaser(purchaser: str, cap: int = 12) -> list[dict]:
    return q(
        """
        SELECT project_id, project_name, package_no, package_total_amount
        FROM project WHERE purchaser = %s
        ORDER BY package_total_amount DESC NULLS LAST LIMIT %s
        """,
        (purchaser, cap),
    )


def _win_map(project_ids: list[str]) -> list[dict]:
    """included 项目的中标方（项目 → 主体名）。"""
    if not project_ids:
        return []
    return q(
        """
        SELECT b.project_id, s.name
        FROM bid b JOIN supplier s ON s.supplier_id = b.supplier_id
        WHERE b.is_winner AND b.project_id = ANY(%s)
        """,
        (project_ids,),
    )


def _bid_map(project_ids: list[str]) -> list[dict]:
    if not project_ids:
        return []
    return q(
        """
        SELECT b.project_id, s.name, b.is_winner
        FROM bid b JOIN supplier s ON s.supplier_id = b.supplier_id
        WHERE b.project_id = ANY(%s)
        """,
        (project_ids,),
    )


def _short(project_name: str, package_no: str, limit: int = 18) -> str:
    name = project_name if len(project_name) <= limit else project_name[: limit - 1] + "…"
    return f"{name} · 包{package_no}"


# ---------------------------------------------------------------------
# S1 采购单位 → 长期/大量合作的中标供应商、产品供应商
# ---------------------------------------------------------------------
def scene_s1(purchaser: str, limit: int = 5) -> dict:
    t0 = perf_counter()
    rows = q(
        """
        WITH win AS (
          SELECT s.name AS subject, count(*) AS coop_times, sum(p.package_total_amount) AS amount
          FROM project p
          JOIN bid b      ON b.project_id = p.project_id AND b.is_winner
          JOIN supplier s ON s.supplier_id = b.supplier_id
          WHERE p.purchaser = %s
          GROUP BY s.name
        ), ps AS (
          SELECT c.product_supplier AS subject, count(DISTINCT c.project_id) AS coop_times,
                 sum(c.total_price) AS amount
          FROM project p JOIN cob c ON c.project_id = p.project_id
          WHERE p.purchaser = %s AND c.product_supplier IS NOT NULL
          GROUP BY c.product_supplier
        )
        SELECT '中标供应商' AS role, subject, coop_times, amount FROM win
        UNION ALL
        SELECT '产品供应商' AS role, subject, coop_times, amount FROM ps
        ORDER BY coop_times DESC
        """,
        (purchaser, purchaser),
    )
    projects = _projects_of_purchaser(purchaser)
    pids = [p["project_id"] for p in projects]
    wins = _win_map(pids)
    supply_pairs = q(
        """
        SELECT DISTINCT project_id, product_supplier FROM cob
        WHERE project_id = ANY(%s) AND product_supplier IS NOT NULL
        """,
        (pids,),
    ) if pids else []

    nodes: list[Node] = [_node(f"buyer:{purchaser}", purchaser, "buyer", len(projects))]
    edges: list[Edge] = []
    for p in projects:
        pid = f"proj:{p['project_id']}"
        nodes.append(_node(pid, _short(p["project_name"], p["package_no"]), "project", p["package_total_amount"]))
        edges.append(_edge(pid, f"buyer:{purchaser}", "buy"))
    seen: set[str] = set()
    for w in wins:
        nid = f"sup:{w['name']}"
        if nid not in seen:
            seen.add(nid)
            times = next((r["coop_times"] for r in rows if r["role"] == "中标供应商" and r["subject"] == w["name"]), 1)
            nodes.append(_node(nid, w["name"], "winner", times))
        edges.append(_edge(f"proj:{w['project_id']}", nid, "win"))
    for sp in supply_pairs:
        nid = f"vendor:{sp['product_supplier']}"
        if nid not in seen:
            seen.add(nid)
            nodes.append(_node(nid, sp["product_supplier"], "vendor", 1))
        edges.append(_edge(f"proj:{sp['project_id']}", nid, "supply"))

    top = rows[:limit]
    highlight = [
        f"sup:{r['subject']}" if r["role"] == "中标供应商" else f"vendor:{r['subject']}" for r in top
    ]
    total_amount = sum(num(r["amount"]) or 0 for r in rows)
    return {
        "scene": "S1",
        "elapsed_ms": round((perf_counter() - t0) * 1000, 1),
        "graph": {"nodes": nodes, "edges": edges, "highlight_ids": highlight},
        "narrative": {
            "title": f"{purchaser} · 长期合作供应商",
            "stats": [
                {"k": "合作主体", "v": str(len(rows))},
                {"k": "合作总次数", "v": str(sum(r["coop_times"] for r in rows))},
                {"k": "交易总金额", "v": yuan(total_amount)},
            ],
            "ranking": [
                {
                    "rank": i + 1,
                    "id": highlight[i],
                    "name": r["subject"],
                    "metrics": {"角色": r["role"], "合作次数": r["coop_times"], "交易金额": num(r["amount"]) or 0},
                }
                for i, r in enumerate(top)
            ],
            "table": {
                "columns": [
                    {"key": "subject", "label": "主体", "align": "left"},
                    {"key": "role", "label": "角色", "align": "left"},
                    {"key": "coop_times", "label": "合作次数", "align": "right"},
                    {"key": "amount", "label": "交易金额", "align": "right"},
                ],
                "rows": [
                    {"subject": r["subject"], "role": r["role"], "coop_times": r["coop_times"], "amount": num(r["amount"])}
                    for r in rows
                ],
            },
        },
    }


# ---------------------------------------------------------------------
# S2 采购单位 → 高频投标主体 + 协同投标组合
# ---------------------------------------------------------------------
def scene_s2(purchaser: str, limit: int = 5) -> dict:
    t0 = perf_counter()
    top_bidders = q(
        """
        SELECT s.name AS subject, count(*) AS bid_times,
               count(*) FILTER (WHERE b.is_winner) AS win_times
        FROM project p
        JOIN bid b      ON b.project_id = p.project_id
        JOIN supplier s ON s.supplier_id = b.supplier_id
        WHERE p.purchaser = %s
        GROUP BY s.name ORDER BY bid_times DESC LIMIT %s
        """,
        (purchaser, limit),
    )
    combos = q(
        """
        SELECT a.name AS subject_a, b.name AS subject_b, count(*) AS together_times
        FROM bid x
        JOIN bid y      ON y.project_id = x.project_id AND y.supplier_id > x.supplier_id
        JOIN project p  ON p.project_id = x.project_id
        JOIN supplier a ON a.supplier_id = x.supplier_id
        JOIN supplier b ON b.supplier_id = y.supplier_id
        WHERE p.purchaser = %s
        GROUP BY a.name, b.name ORDER BY together_times DESC LIMIT %s
        """,
        (purchaser, limit),
    )
    totals = q1(
        """
        SELECT count(DISTINCT b.supplier_id) AS bidders, count(DISTINCT p.project_id) AS projects,
               count(*) AS bids
        FROM project p JOIN bid b ON b.project_id = p.project_id
        WHERE p.purchaser = %s
        """,
        (purchaser,),
    ) or {"bidders": 0, "projects": 0, "bids": 0}

    projects = _projects_of_purchaser(purchaser)
    pids = [p["project_id"] for p in projects]
    bids = _bid_map(pids)

    nodes: list[Node] = [_node(f"buyer:{purchaser}", purchaser, "buyer", totals["projects"])]
    edges: list[Edge] = []
    for p in projects:
        pid = f"proj:{p['project_id']}"
        nodes.append(_node(pid, _short(p["project_name"], p["package_no"]), "project", p["package_total_amount"]))
        edges.append(_edge(pid, f"buyer:{purchaser}", "buy"))
    top_names = {r["subject"] for r in top_bidders}
    for c in combos:  # 协同组合里的主体也保证出现
        top_names.update([c["subject_a"], c["subject_b"]])
    meta = {r["subject"]: r for r in top_bidders}
    for name in sorted(top_names):
        m = meta.get(name)
        nodes.append(_node(f"sup:{name}", name, "winner" if (m and m["win_times"]) else "bidder", m["bid_times"] if m else 1))
    for b in bids:
        if b["name"] in top_names:
            edges.append(_edge(f"proj:{b['project_id']}", f"sup:{b['name']}", "win" if b["is_winner"] else "bid"))

    highlight = [f"sup:{r['subject']}" for r in top_bidders]
    return {
        "scene": "S2",
        "elapsed_ms": round((perf_counter() - t0) * 1000, 1),
        "graph": {"nodes": nodes, "edges": edges, "highlight_ids": highlight},
        "narrative": {
            "title": f"{purchaser} · 高频投标主体与协同组合",
            "stats": [
                {"k": "投标主体", "v": str(totals["bidders"])},
                {"k": "竞标项目", "v": str(totals["projects"])},
                {"k": "投标次数", "v": str(totals["bids"])},
            ],
            "ranking": [
                {
                    "rank": i + 1,
                    "id": f"sup:{r['subject']}",
                    "name": r["subject"],
                    "metrics": {"投标次数": r["bid_times"], "中标次数": r["win_times"]},
                }
                for i, r in enumerate(top_bidders)
            ],
            "combos": [
                {"a": c["subject_a"], "b": c["subject_b"], "count": c["together_times"]} for c in combos
            ],
        },
    }


# ---------------------------------------------------------------------
# S3 中标供应商 → 高频共同竞标主体
# ---------------------------------------------------------------------
def scene_s3(winner: str, limit: int = 5) -> dict:
    t0 = perf_counter()
    rows = q(
        """
        WITH my_projects AS (
          SELECT b.project_id
          FROM bid b JOIN supplier s ON s.supplier_id = b.supplier_id
          WHERE s.name = %s AND b.is_winner
        )
        SELECT s.name AS subject, count(*) AS co_bid_times,
               sum(p.package_total_amount) AS amount
        FROM bid b
        JOIN my_projects mp ON mp.project_id = b.project_id
        JOIN supplier s     ON s.supplier_id = b.supplier_id
        JOIN project p      ON p.project_id = b.project_id
        WHERE s.name <> %s
        GROUP BY s.name ORDER BY co_bid_times DESC LIMIT %s
        """,
        (winner, winner, limit),
    )
    totals = q1(
        """
        WITH my_projects AS (
          SELECT b.project_id
          FROM bid b JOIN supplier s ON s.supplier_id = b.supplier_id
          WHERE s.name = %s AND b.is_winner
        )
        SELECT count(DISTINCT mp.project_id) AS projects,
               (SELECT count(DISTINCT b.supplier_id) FROM bid b JOIN my_projects mp ON mp.project_id = b.project_id) - 1 AS parties,
               (SELECT sum(package_total_amount) FROM project p JOIN my_projects mp ON mp.project_id = p.project_id) AS amount
        FROM my_projects mp
        """,
        (winner,),
    ) or {"projects": 0, "parties": 0, "amount": None}

    projects = q(
        """
        SELECT p.project_id, p.project_name, p.package_no, p.package_total_amount
        FROM project p
        JOIN bid b      ON b.project_id = p.project_id AND b.is_winner
        JOIN supplier s ON s.supplier_id = b.supplier_id
        WHERE s.name = %s
        ORDER BY p.package_total_amount DESC NULLS LAST LIMIT 12
        """,
        (winner,),
    )
    pids = [p["project_id"] for p in projects]
    bids = _bid_map(pids)
    co_names = {r["subject"] for r in rows}

    nodes: list[Node] = [_node(f"sup:{winner}", winner, "winner", totals["projects"])]
    edges: list[Edge] = []
    for p in projects:
        pid = f"proj:{p['project_id']}"
        nodes.append(_node(pid, _short(p["project_name"], p["package_no"]), "project", p["package_total_amount"]))
        edges.append(_edge(pid, f"sup:{winner}", "win"))
    meta = {r["subject"]: r for r in rows}
    for name in sorted(co_names):
        nodes.append(_node(f"sup:{name}", name, "bidder", meta[name]["co_bid_times"]))
    for b in bids:
        if b["name"] in co_names:
            edges.append(_edge(f"proj:{b['project_id']}", f"sup:{b['name']}", "win" if b["is_winner"] else "bid"))

    highlight = [f"sup:{r['subject']}" for r in rows]
    return {
        "scene": "S3",
        "elapsed_ms": round((perf_counter() - t0) * 1000, 1),
        "graph": {"nodes": nodes, "edges": edges, "highlight_ids": highlight},
        "narrative": {
            "title": f"{winner} · 高频共同竞标主体",
            "stats": [
                {"k": "中标项目", "v": str(totals["projects"])},
                {"k": "共同竞标主体", "v": str(totals["parties"])},
                {"k": "涉及金额", "v": yuan(totals["amount"])},
            ],
            "ranking": [
                {
                    "rank": i + 1,
                    "id": f"sup:{r['subject']}",
                    "name": r["subject"],
                    "metrics": {"共同竞标": r["co_bid_times"], "涉及金额": num(r["amount"]) or 0},
                }
                for i, r in enumerate(rows)
            ],
        },
    }


# ---------------------------------------------------------------------
# S4 多中标供应商 → 共同合作的采购单位
# ---------------------------------------------------------------------
def scene_s4(suppliers: list[str], limit: int = 20) -> dict:
    t0 = perf_counter()
    rows = q(
        """
        WITH subj AS (
          SELECT supplier_id FROM supplier WHERE name = ANY(%s)
        ), per AS (
          SELECT p.purchaser, b.supplier_id, count(*) AS times,
                 sum(p.package_total_amount) AS amount
          FROM project p
          JOIN bid b ON b.project_id = p.project_id AND b.is_winner
          WHERE b.supplier_id IN (SELECT supplier_id FROM subj)
          GROUP BY p.purchaser, b.supplier_id
        )
        SELECT purchaser, count(*) AS subject_count,
               sum(times) AS coop_times, sum(amount) AS coop_amount
        FROM per
        GROUP BY purchaser
        HAVING count(*) = (SELECT count(*) FROM subj)
        ORDER BY coop_times DESC LIMIT %s
        """,
        (suppliers, limit),
    )
    per_pair = q(
        """
        WITH subj AS (SELECT supplier_id FROM supplier WHERE name = ANY(%s))
        SELECT p.purchaser, s.name, count(*) AS times, sum(p.package_total_amount) AS amount
        FROM project p
        JOIN bid b ON b.project_id = p.project_id AND b.is_winner
        JOIN supplier s ON s.supplier_id = b.supplier_id
        WHERE b.supplier_id IN (SELECT supplier_id FROM subj)
        GROUP BY p.purchaser, s.name
        """,
        (suppliers,),
    )

    nodes: list[Node] = []
    edges: list[Edge] = []
    for name in suppliers:
        wins = sum(p["times"] for p in per_pair if p["name"] == name)
        nodes.append(_node(f"sup:{name}", name, "winner", wins))
    purchasers = {r["purchaser"] for r in rows}
    for r in rows:
        nodes.append(_node(f"buyer:{r['purchaser']}", r["purchaser"], "buyer", r["coop_times"]))
    for p in per_pair:
        if p["purchaser"] in purchasers:
            edges.append(_edge(f"sup:{p['name']}", f"buyer:{p['purchaser']}", "win", p["times"]))

    highlight = [f"buyer:{r['purchaser']}" for r in rows]
    return {
        "scene": "S4",
        "elapsed_ms": round((perf_counter() - t0) * 1000, 1),
        "graph": {"nodes": nodes, "edges": edges, "highlight_ids": highlight},
        "narrative": {
            "title": f"{'、'.join(suppliers)} · 共同合作的采购单位",
            "stats": [
                {"k": "共同采购单位", "v": str(len(rows))},
                {"k": "合作总次数", "v": str(sum(r["coop_times"] for r in rows))},
                {"k": "合作总金额", "v": yuan(sum(num(r["coop_amount"]) or 0 for r in rows))},
            ],
            "ranking": [
                {
                    "rank": i + 1,
                    "id": f"buyer:{r['purchaser']}",
                    "name": r["purchaser"],
                    "metrics": {"合作次数": r["coop_times"], "合作金额": num(r["coop_amount"]) or 0},
                }
                for i, r in enumerate(rows[:5])
            ],
            "table": {
                "columns": [
                    {"key": "purchaser", "label": "采购单位", "align": "left"},
                    {"key": "coop_times", "label": "合作次数", "align": "right"},
                    {"key": "coop_amount", "label": "合作金额", "align": "right"},
                ],
                "rows": [
                    {"purchaser": r["purchaser"], "coop_times": r["coop_times"], "coop_amount": num(r["coop_amount"])}
                    for r in rows
                ],
            },
        },
    }


# ---------------------------------------------------------------------
# S5 多中标供应商 → 共同竞标的项目与结果
# ---------------------------------------------------------------------
def scene_s5(suppliers: list[str], limit: int = 20) -> dict:
    t0 = perf_counter()
    rows = q(
        """
        WITH subj AS (
          SELECT supplier_id FROM supplier WHERE name = ANY(%s)
        )
        SELECT p.project_id, p.project_name, p.package_no, p.purchaser, p.package_total_amount,
               count(DISTINCT b.supplier_id) AS subject_count,
               string_agg(DISTINCT s.name, '、') FILTER (WHERE b.is_winner) AS winners
        FROM project p
        JOIN bid b      ON b.project_id = p.project_id
        JOIN supplier s ON s.supplier_id = b.supplier_id
        WHERE b.supplier_id IN (SELECT supplier_id FROM subj)
        GROUP BY p.project_id, p.project_name, p.package_no, p.purchaser, p.package_total_amount
        HAVING count(DISTINCT b.supplier_id) = (SELECT count(*) FROM subj)
        ORDER BY p.package_total_amount DESC NULLS LAST LIMIT %s
        """,
        (suppliers, limit),
    )
    totals = q1(
        """
        WITH subj AS (SELECT supplier_id FROM supplier WHERE name = ANY(%s)),
        common AS (
          SELECT p.project_id, p.package_total_amount
          FROM project p JOIN bid b ON b.project_id = p.project_id
          WHERE b.supplier_id IN (SELECT supplier_id FROM subj)
          GROUP BY p.project_id, p.package_total_amount
          HAVING count(DISTINCT b.supplier_id) = (SELECT count(*) FROM subj)
        )
        SELECT count(*) AS projects, sum(package_total_amount) AS amount FROM common
        """,
        (suppliers,),
    ) or {"projects": 0, "amount": None}

    pids = [r["project_id"] for r in rows]
    bids = _bid_map(pids)
    subj_set = set(suppliers)

    nodes: list[Node] = []
    edges: list[Edge] = []
    for name in suppliers:
        wins = sum(1 for r in rows if r["winners"] and name in r["winners"])
        nodes.append(_node(f"sup:{name}", name, "winner", max(wins, 1)))
    for r in rows:
        nodes.append(_node(f"proj:{r['project_id']}", _short(r["project_name"], r["package_no"]), "project", r["package_total_amount"]))
    for b in bids:
        if b["name"] in subj_set:
            edges.append(_edge(f"proj:{b['project_id']}", f"sup:{b['name']}", "win" if b["is_winner"] else "bid"))

    highlight = [f"proj:{r['project_id']}" for r in rows[:5]]
    return {
        "scene": "S5",
        "elapsed_ms": round((perf_counter() - t0) * 1000, 1),
        "graph": {"nodes": nodes, "edges": edges, "highlight_ids": highlight},
        "narrative": {
            "title": f"{'、'.join(suppliers)} · 共同竞标的项目",
            "stats": [
                {"k": "共同竞标项目", "v": str(totals["projects"])},
                {"k": "项目总金额", "v": yuan(totals["amount"])},
                {"k": "参与主体", "v": str(len(suppliers))},
            ],
            "ranking": [
                {
                    "rank": i + 1,
                    "id": f"proj:{r['project_id']}",
                    "name": _short(r["project_name"], r["package_no"], 30),
                    "metrics": {"包金额": num(r["package_total_amount"]) or 0},
                }
                for i, r in enumerate(rows[:5])
            ],
            "table": {
                "columns": [
                    {"key": "project_name", "label": "项目", "align": "left"},
                    {"key": "purchaser", "label": "采购单位", "align": "left"},
                    {"key": "amount", "label": "包金额", "align": "right"},
                    {"key": "winners", "label": "中标方", "align": "left"},
                ],
                "rows": [
                    {
                        "project_name": _short(r["project_name"], r["package_no"], 30),
                        "purchaser": r["purchaser"],
                        "amount": num(r["package_total_amount"]),
                        "winners": r["winners"] or "—",
                    }
                    for r in rows
                ],
            },
        },
    }


SCENES = {"S1": scene_s1, "S2": scene_s2, "S3": scene_s3, "S4": scene_s4, "S5": scene_s5}


# ---------------------------------------------------------------------
# 星图总览（采样：采购单位全量 + 项目全量 + 高频主体 TOP N）
# ---------------------------------------------------------------------
def overview(supplier_cap: int = 220, min_bids: int = 2) -> dict:
    t0 = perf_counter()
    purchasers = q(
        "SELECT purchaser AS name, count(*) AS weight FROM project WHERE purchaser IS NOT NULL GROUP BY purchaser"
    )
    projects = q(
        "SELECT project_id, project_name, package_no, purchaser, coalesce(package_total_amount, 0) AS amount FROM project"
    )
    suppliers = q(
        """
        SELECT s.name, count(*) AS bids, count(*) FILTER (WHERE b.is_winner) AS wins
        FROM bid b JOIN supplier s ON s.supplier_id = b.supplier_id
        GROUP BY s.name ORDER BY bids DESC
        """
    )
    kept = [s for s in suppliers if s["bids"] >= min_bids][:supplier_cap]
    kept_names = {s["name"] for s in kept}

    bid_edges = q(
        """
        SELECT b.project_id, s.name, b.is_winner
        FROM bid b JOIN supplier s ON s.supplier_id = b.supplier_id
        """
    )

    nodes: list[Node] = []
    edges: list[Edge] = []
    for p in purchasers:
        nodes.append(_node(f"buyer:{p['name']}", p["name"], "buyer", p["weight"]))
    for p in projects:
        pid = f"proj:{p['project_id']}"
        nodes.append(_node(pid, _short(p["project_name"], p["package_no"]), "project", p["amount"]))
        if p["purchaser"]:
            edges.append(_edge(pid, f"buyer:{p['purchaser']}", "buy"))
    for s in kept:
        kind = "winner" if s["wins"] else "bidder"
        nodes.append(_node(f"sup:{s['name']}", s["name"], kind, s["bids"]))
    for e in bid_edges:
        if e["name"] in kept_names:
            edges.append(_edge(f"proj:{e['project_id']}", f"sup:{e['name']}", "win" if e["is_winner"] else "bid"))

    return {
        "elapsed_ms": round((perf_counter() - t0) * 1000, 1),
        "graph": {"nodes": nodes, "edges": edges, "highlight_ids": []},
        "meta": {
            "purchasers": len(purchasers),
            "projects": len(projects),
            "suppliers_total": len(suppliers),
            "suppliers_shown": len(kept),
            "sampled": len(kept) < len(suppliers),
        },
    }
