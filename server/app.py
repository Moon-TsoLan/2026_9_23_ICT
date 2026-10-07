"""ICT 赛题五 · 可视化分析检索平台 —— 后端薄层。

职责：把 db/queries.sql 里的只读查询包装成 REST API，供 Vue 前端消费。
不做抽取、不做写入；上传/触发处理在本轮返回 501（接管线是下一轮的事）。

启动：  D:\\python\\python.exe -m uvicorn server.app:app --port 8000
"""
from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .db import num, q, q1, yuan
from .scenes import SCENES, overview

ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = ROOT / "work" / "runs"

app = FastAPI(title="ICT 标络 · 数据 API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

SceneId = Literal["S1", "S2", "S3", "S4", "S5"]


# ---------------------------------------------------------------------
# 健康检查
# ---------------------------------------------------------------------
@app.get("/api/health")
def health() -> dict:
    row = q1("SELECT count(*) AS n FROM announcement")
    return {"ok": True, "announcements": row["n"] if row else 0}


# ---------------------------------------------------------------------
# 标的物检索（功能 2）
# ---------------------------------------------------------------------
SORTS = {
    "relevance": "total_price DESC NULLS LAST",
    "total_price_desc": "total_price DESC NULLS LAST",
    "total_price_asc": "total_price ASC NULLS LAST",
    "unit_price_desc": "unit_price DESC NULLS LAST",
}
PRICE_FIELDS = {"unit_price", "total_price"}
SOURCE_TYPES = {"html", "pdf", "docx", "doc", "xlsx"}


def _build_search(
    kw: str = "",
    purchaser: str = "",
    winner: str = "",
    product_supplier: str = "",
    project_name: str = "",
    brand: str = "",
    category_type: str = "",
    source_type: str = "",
    price_field: str = "total_price",
    price_min: float | None = None,
    price_max: float | None = None,
    sort: str = "relevance",
) -> tuple[str, list[Any], str]:
    conds = ["TRUE"]
    params: list[Any] = []
    if kw:
        like = f"%{kw}%"
        conds.append(
            "(object_name ILIKE %s OR project_name ILIKE %s OR brand ILIKE %s"
            " OR spec_model ILIKE %s OR winner_name ILIKE %s OR purchaser ILIKE %s)"
        )
        params += [like] * 6
    if purchaser:
        conds.append("purchaser = %s")
        params.append(purchaser)
    if winner:
        conds.append("winner_name = %s")
        params.append(winner)
    if product_supplier:
        conds.append("product_supplier = %s")
        params.append(product_supplier)
    if project_name:
        conds.append("project_name ILIKE %s")
        params.append(f"%{project_name}%")
    if brand:
        conds.append("brand ILIKE %s")
        params.append(f"%{brand}%")
    if category_type in ("A", "B", "C"):
        conds.append("category_type = %s")
        params.append(category_type)
    sources = [s for s in source_type.split(",") if s in SOURCE_TYPES]
    if sources:
        conds.append("source_type = ANY(%s)")
        params.append(sources)
    field = price_field if price_field in PRICE_FIELDS else "total_price"
    if price_min is not None:
        conds.append(f"{field} >= %s")
        params.append(price_min)
    if price_max is not None:
        conds.append(f"{field} <= %s")
        params.append(price_max)
    order = SORTS.get(sort, SORTS["relevance"])
    return " AND ".join(conds), params, order


def _cob_item(r: dict) -> dict:
    return {
        "cob_id": r["cob_id"],
        "project_id": r["project_id"],
        "project_name": r["project_name"],
        "package_no": r["package_no"],
        "purchaser": r["purchaser"],
        "package_total_amount": num(r["package_total_amount"]),
        "object_name": r["object_name"],
        "category_code": r["category_code"],
        "category_name": r["category_name"],
        "category_type": r["category_type"],
        "brand": r["brand"],
        "product_supplier": r["product_supplier"],
        "spec_model": r["spec_model"],
        "unit_price": num(r["unit_price"]),
        "quantity": num(r["quantity"]),
        "unit": r["unit"],
        "total_price": num(r["total_price"]),
        "winner": {"supplier_name": r["winner_name"], "score": num(r["winner_score"])} if r["winner_name"] else None,
        "announcement_id": r["announcement_id"],
        "provenance": {"source_type": r["source_type"], "file_id": r["source_file_id"]},
    }


@app.get("/api/objects/search")
def objects_search(
    kw: str = "",
    purchaser: str = "",
    winner: str = "",
    product_supplier: str = "",
    project_name: str = "",
    brand: str = "",
    category_type: str = "",
    source_type: str = "",
    price_field: str = "total_price",
    price_min: float | None = None,
    price_max: float | None = None,
    sort: str = "relevance",
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> dict:
    where, params, order = _build_search(
        kw, purchaser, winner, product_supplier, project_name, brand,
        category_type, source_type, price_field, price_min, price_max, sort,
    )
    total = (q1(f"SELECT count(*) AS n FROM v_cob_record WHERE {where}", params) or {"n": 0})["n"]
    rows = q(
        f"SELECT * FROM v_cob_record WHERE {where} ORDER BY {order} LIMIT %s OFFSET %s",
        [*params, page_size, (page - 1) * page_size],
    )
    return {"total": total, "page": page, "page_size": page_size, "items": [_cob_item(r) for r in rows]}


@app.get("/api/objects/export")
def objects_export(
    kw: str = "",
    purchaser: str = "",
    winner: str = "",
    product_supplier: str = "",
    project_name: str = "",
    brand: str = "",
    category_type: str = "",
    source_type: str = "",
    price_field: str = "total_price",
    price_min: float | None = None,
    price_max: float | None = None,
    sort: str = "relevance",
) -> Response:
    where, params, order = _build_search(
        kw, purchaser, winner, product_supplier, project_name, brand,
        category_type, source_type, price_field, price_min, price_max, sort,
    )
    rows = q(f"SELECT * FROM v_cob_record WHERE {where} ORDER BY {order} LIMIT 5000", params)
    buf = io.StringIO()
    buf.write("﻿")  # BOM：保证 Excel 打开中文不乱码
    writer = csv.writer(buf)
    writer.writerow([
        "标的物名称", "品目编码", "品目名称", "品目类别", "品牌", "产品供应商", "规格型号",
        "单价(元)", "数量", "单位", "总价(元)", "项目名称", "包号", "采购单位",
        "中标供应商", "中标得分", "来源公告", "来源类型",
    ])
    for r in rows:
        writer.writerow([
            r["object_name"], r["category_code"], r["category_name"], r["category_type"],
            r["brand"], r["product_supplier"], r["spec_model"],
            num(r["unit_price"]), num(r["quantity"]), r["unit"], num(r["total_price"]),
            r["project_name"], r["package_no"], r["purchaser"],
            r["winner_name"], num(r["winner_score"]), r["announcement_id"], r["source_type"],
        ])
    return Response(
        buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="cob_export.csv"'},
    )


@app.get("/api/objects/{cob_id}")
def objects_detail(cob_id: int) -> dict:
    row = q1("SELECT * FROM v_cob_record WHERE cob_id = %s", (cob_id,))
    if not row:
        raise HTTPException(404, "标的物不存在")
    bidders = q(
        """
        SELECT s.name, b.score, b.is_winner
        FROM bid b JOIN supplier s ON s.supplier_id = b.supplier_id
        WHERE b.project_id = %s
        ORDER BY b.is_winner DESC, b.score DESC NULLS LAST
        """,
        (row["project_id"],),
    )
    item = _cob_item(row)
    item["bidders"] = [
        {"supplier_name": b["name"], "score": num(b["score"]), "is_winner": b["is_winner"]} for b in bidders
    ]
    return item


# ---------------------------------------------------------------------
# 五大场景（功能 3）
# ---------------------------------------------------------------------
class SceneQuery(BaseModel):
    subjects: list[str] = Field(min_length=1)
    limit: int = Field(5, ge=1, le=50)


@app.post("/api/scenes/{scene_id}/query")
def scene_query(scene_id: SceneId, body: SceneQuery) -> dict:
    subjects = [s.strip() for s in body.subjects if s.strip()]
    if not subjects:
        raise HTTPException(422, "subjects 不能为空")
    if scene_id in ("S1", "S2"):
        if len(subjects) != 1:
            raise HTTPException(422, f"{scene_id} 需要 1 个采购单位")
        return SCENES[scene_id](subjects[0], body.limit)
    if scene_id == "S3":
        if len(subjects) != 1:
            raise HTTPException(422, "S3 需要 1 个中标供应商")
        return SCENES["S3"](subjects[0], body.limit)
    # S4 / S5 多主体
    if len(subjects) < 2:
        raise HTTPException(422, f"{scene_id} 需要至少 2 个中标供应商")
    return SCENES[scene_id](subjects, max(body.limit, 20))


@app.get("/api/graph/overview")
def graph_overview() -> dict:
    return overview()


# ---------------------------------------------------------------------
# 主体（检索 + 档案）
# ---------------------------------------------------------------------
@app.get("/api/parties")
def parties_search(q_: str = Query("", alias="q")) -> dict:
    kw = q_.strip()
    if not kw:
        return {"items": []}
    like = f"%{kw}%"
    suppliers = q(
        """
        SELECT s.name,
               EXISTS(SELECT 1 FROM bid b WHERE b.supplier_id = s.supplier_id AND b.is_winner) AS has_win,
               similarity(s.norm_name, %s) AS sim
        FROM supplier s
        WHERE s.norm_name ILIKE %s
        ORDER BY sim DESC LIMIT 8
        """,
        (kw, like),
    )
    purchasers = q(
        """
        SELECT purchaser AS name, count(*) AS projects
        FROM project WHERE purchaser ILIKE %s
        GROUP BY purchaser ORDER BY projects DESC LIMIT 5
        """,
        (like,),
    )
    # 项目节点：03 探索的星图上 proj: 是一等节点，搜索也必须能定位到它，
    # 否则用户在星图上看见一颗项目星，却搜不到它。
    projects = q(
        """
        SELECT project_id, project_name, package_no, round_no
        FROM project
        WHERE project_name ILIKE %s
        ORDER BY package_total_amount DESC NULLS LAST, project_id
        LIMIT 8
        """,
        (like,),
    )
    items = [
        {"id": f"buyer:{p['name']}", "name": p["name"], "kind": "buyer"} for p in purchasers
    ] + [
        {"id": f"proj:{p['project_id']}", "name": _project_label(p["project_name"], p["package_no"], p["round_no"]),
         "kind": "project"}
        for p in projects
    ] + [
        {"id": f"sup:{s['name']}", "name": s["name"], "kind": "winner" if s["has_win"] else "bidder"}
        for s in suppliers
    ]
    return {"items": items}


def _project_label(project_name: str, package_no: str, round_no: int = 1) -> str:
    """星图上项目节点的显示名；与 server/scenes.py 的 _short 保持同一种读法。"""
    suffix = f" · 包{package_no}" + (f"（第{round_no}轮）" if round_no > 1 else "")
    return f"{project_name}{suffix}"


@app.get("/api/parties/{party_id:path}")
def party_profile(party_id: str) -> dict:
    kind, _, name = party_id.partition(":")
    if kind == "buyer":
        return _buyer_profile(party_id, name)
    if kind == "proj":
        return _project_profile(party_id, name)
    if kind in ("sup", "vendor"):
        # 星图里的"产品供应商"节点写作 vendor:<名称>，实体可能仍在 supplier 表里。
        # 若按名称取不到（有些产品供应商从未作为投标主体入库），
        # 就退回"按名称找供应商档案"，而不是直接 404 —— 前端因此不再报错。
        try:
            return _supplier_profile(party_id, name)
        except HTTPException:
            if kind == "vendor":
                row = q1("SELECT name FROM supplier WHERE name = %s LIMIT 1", (name,))
                if row:
                    return _supplier_profile("sup:" + name, row["name"])
            raise
    raise HTTPException(422, "id 需形如 sup:/vendor:/buyer:/proj:<id>")


def _project_profile(pid: str, project_id: str) -> dict:
    """项目（= 一个包一轮）的档案：采购单位、编号、中标/投标方。"""
    base = q1(
        """
        SELECT p.project_id, p.project_name, p.package_no, p.round_no, p.purchaser,
               p.package_total_amount, p.source_project_no, p.announcement_id,
               (SELECT count(*) FROM cob c WHERE c.project_id = p.project_id) AS cobs
        FROM project p WHERE p.project_id = %s
        """,
        (project_id,),
    )
    if not base:
        raise HTTPException(404, "项目不存在")
    bidders = q(
        """
        SELECT s.name, b.is_winner
        FROM bid b JOIN supplier s ON s.supplier_id = b.supplier_id
        WHERE b.project_id = %s
        ORDER BY b.is_winner DESC, b.score DESC NULLS LAST
        """,
        (project_id,),
    )
    winners = [b["name"] for b in bidders if b["is_winner"]]
    facts = [
        f"中标方：{'、'.join(winners)}" if winners else "还没有中标记录",
        f"投标方 {len(bidders)} 家",
    ]
    if base["purchaser"]:
        facts.append(f"采购单位：{base['purchaser']}")
    if base["source_project_no"]:
        facts.append(f"项目编号：{base['source_project_no']}")
    if base["round_no"] > 1:
        facts.append(f"同一（项目编号, 包号）的第 {base['round_no']} 轮公告")
    facts.append(f"公告：{base['announcement_id']}")
    return {
        "id": pid,
        "name": _project_label(base["project_name"], base["package_no"], base["round_no"]),
        "project_name": base["project_name"],
        "kind": "project",
        "stats": [
            {"k": "包金额", "v": yuan(base["package_total_amount"])},
            {"k": "标的物", "v": str(base["cobs"])},
            {"k": "投标方", "v": str(len(bidders))},
            {"k": "轮次", "v": str(base["round_no"])},
        ],
        "facts": facts,
    }


def _buyer_profile(pid: str, name: str) -> dict:
    base = q1(
        """
        SELECT count(DISTINCT p.project_id) AS projects,
               (SELECT count(*) FROM cob c JOIN project p2 ON p2.project_id = c.project_id WHERE p2.purchaser = %s) AS cobs,
               sum(p.package_total_amount) AS amount
        FROM project p WHERE p.purchaser = %s
        """,
        (name, name),
    )
    if not base or not base["projects"]:
        raise HTTPException(404, "采购单位不存在")
    winners = q(
        """
        SELECT s.name, count(*) AS wins, sum(p.package_total_amount) AS amount
        FROM project p
        JOIN bid b      ON b.project_id = p.project_id AND b.is_winner
        JOIN supplier s ON s.supplier_id = b.supplier_id
        WHERE p.purchaser = %s
        GROUP BY s.name ORDER BY wins DESC, amount DESC NULLS LAST LIMIT 3
        """,
        (name,),
    )
    facts = [
        f"{w['name']} 中标 {w['wins']} 次，累计 {yuan(w['amount'])}" for w in winners
    ]
    return {
        "id": pid,
        "name": name,
        "kind": "buyer",
        "stats": [
            {"k": "项目", "v": str(base["projects"])},
            {"k": "标的物", "v": str(base["cobs"])},
            {"k": "包金额合计", "v": yuan(base["amount"])},
            {"k": "中标供应商", "v": str(len(winners))},
        ],
        "facts": facts,
    }


def _vendor_profile(pid: str, name: str) -> dict | None:
    """产品供应商的降级档案：只依据 cob.product_supplier 聚合。
    这些主体在星图上是合法节点（标的物里的厂商/经销商），但从未投标，因此没有 supplier 行。"""
    base = q1(
        """
        SELECT count(*) AS cobs,
               count(DISTINCT c.project_id) AS projects,
               sum(c.total_price) AS amount
        FROM cob c WHERE c.product_supplier = %s
        """,
        (name,),
    )
    if not base or not base["cobs"]:
        return None
    buyers = q(
        """
        SELECT p.purchaser, count(*) AS times
        FROM cob c JOIN project p ON p.project_id = c.project_id
        WHERE c.product_supplier = %s AND p.purchaser IS NOT NULL
        GROUP BY p.purchaser ORDER BY times DESC LIMIT 3
        """,
        (name,),
    )
    facts = [f"作为产品供应商出现在 {b['purchaser']} 的 {b['times']} 个标的物中" for b in buyers]
    return {
        "id": pid,
        "name": name,
        "kind": "vendor",
        "stats": [
            {"k": "供货标的物", "v": str(base["cobs"])},
            {"k": "涉及项目", "v": str(base["projects"])},
            {"k": "标的物金额", "v": yuan(base["amount"])},
            {"k": "采购单位", "v": str(len(buyers))},
        ],
        "facts": facts,
    }


def _supplier_profile(pid: str, name: str) -> dict:
    base = q1(
        """
        SELECT count(*) AS bids,
               count(*) FILTER (WHERE b.is_winner) AS wins,
               sum(p.package_total_amount) FILTER (WHERE b.is_winner) AS won_amount
        FROM bid b JOIN project p ON p.project_id = b.project_id
        JOIN supplier s ON s.supplier_id = b.supplier_id
        WHERE s.name = %s
        """,
        (name,),
    )
    if not base or not base["bids"]:
        # 该名称可能只是"产品供应商"（出现在 cob.product_supplier），从未作为投标主体入库。
        # 它在星图上是合法节点，因此给一个基于标的物的降级档案，而不是 404。
        fallback = _vendor_profile(pid, name)
        if fallback:
            return fallback
        raise HTTPException(404, "主体不存在")
    co = q(
        """
        WITH mine AS (
          SELECT b.project_id FROM bid b JOIN supplier s ON s.supplier_id = b.supplier_id
          WHERE s.name = %s
        )
        SELECT s2.name, count(*) AS times
        FROM bid b2 JOIN mine m ON m.project_id = b2.project_id
        JOIN supplier s2 ON s2.supplier_id = b2.supplier_id
        WHERE s2.name <> %s
        GROUP BY s2.name ORDER BY times DESC LIMIT 3
        """,
        (name, name),
    )
    buyers = q(
        """
        SELECT p.purchaser, count(*) AS wins
        FROM bid b JOIN project p ON p.project_id = b.project_id
        JOIN supplier s ON s.supplier_id = b.supplier_id
        WHERE s.name = %s AND b.is_winner AND p.purchaser IS NOT NULL
        GROUP BY p.purchaser ORDER BY wins DESC LIMIT 3
        """,
        (name,),
    )
    facts = [f"与 {c['name']} 同场竞标 {c['times']} 次" for c in co]
    facts += [f"在 {b['purchaser']} 中标 {b['wins']} 次" for b in buyers]
    return {
        "id": pid,
        "name": name,
        "kind": "winner" if base["wins"] else "bidder",
        "stats": [
            {"k": "参与投标", "v": str(base["bids"])},
            {"k": "中标", "v": str(base["wins"])},
            {"k": "中标金额", "v": yuan(base["won_amount"])},
            {"k": "同场主体", "v": str(len(co)) + "+"},
        ],
        "facts": facts,
    }


# ---------------------------------------------------------------------
# 全局分布统计
# ---------------------------------------------------------------------
@app.get("/api/stats/distribution")
def stats_distribution() -> dict:
    categories = q(
        """
        SELECT coalesce(category_type, '未标') AS key, count(*) AS count, sum(total_price) AS amount
        FROM cob GROUP BY category_type ORDER BY amount DESC NULLS LAST
        """
    )
    brands = q(
        """
        SELECT brand AS key, count(*) AS count, sum(total_price) AS amount
        FROM cob WHERE brand IS NOT NULL
        GROUP BY brand ORDER BY count DESC LIMIT 10
        """
    )
    purchasers = q(
        """
        SELECT purchaser AS key, count(*) AS count, sum(package_total_amount) AS amount
        FROM project WHERE purchaser IS NOT NULL
        GROUP BY purchaser ORDER BY amount DESC NULLS LAST LIMIT 10
        """
    )
    winners = q(
        """
        SELECT s.name AS key, count(*) AS count, sum(p.package_total_amount) AS amount
        FROM bid b JOIN supplier s ON s.supplier_id = b.supplier_id
        JOIN project p ON p.project_id = b.project_id
        WHERE b.is_winner
        GROUP BY s.name ORDER BY count DESC LIMIT 10
        """
    )
    pack = lambda rows: [{"key": r["key"], "count": r["count"], "amount": num(r["amount"])} for r in rows]
    return {
        "categories": pack(categories),
        "brands": pack(brands),
        "purchasers": pack(purchasers),
        "winners": pack(winners),
    }


# ---------------------------------------------------------------------
# 公告与处理流水线（功能 1：真实公告列表 + run_state 回放）
# ---------------------------------------------------------------------
@app.get("/api/announcements")
def announcements() -> dict:
    rows = q(
        """
        SELECT a.announcement_id, a.title, a.announcement_type, a.run_status,
               a.review_required, a.created_at,
               (a.raw_json ? '_synthetic') AS synthetic,
               count(DISTINCT p.project_id) AS projects,
               (SELECT count(*) FROM cob c JOIN project p2 ON p2.project_id = c.project_id
                WHERE p2.announcement_id = a.announcement_id) AS cobs,
               (SELECT p3.project_name FROM project p3
                WHERE p3.announcement_id = a.announcement_id LIMIT 1) AS first_project
        FROM announcement a
        LEFT JOIN project p ON p.announcement_id = a.announcement_id
        GROUP BY a.announcement_id
        ORDER BY a.created_at DESC
        """
    )
    return {
        "total": len(rows),
        "items": [
            {
                "announcement_id": r["announcement_id"],
                "title": r["title"] or r["first_project"] or r["announcement_id"],
                "announcement_type": r["announcement_type"],
                "run_status": r["run_status"],
                "review_required": r["review_required"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "synthetic": r["synthetic"],
                "projects": r["projects"],
                "cobs": r["cobs"],
            }
            for r in rows
        ],
    }


@app.get("/api/runs/{announcement_id}")
def run_state(announcement_id: str) -> dict:
    if not re.fullmatch(r"[0-9A-Za-z_]+", announcement_id):
        raise HTTPException(422, "announcement_id 非法")
    path = RUNS_DIR / announcement_id / "run_state.json"
    if not path.exists():
        raise HTTPException(404, "该公告没有处理记录（演示数据直接入库，未经过管线）")
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------
# 上传 / 触发处理：本轮不接管线，明确 501
# ---------------------------------------------------------------------
@app.post("/api/ingest/upload")
def ingest_upload() -> None:
    raise HTTPException(501, "演示环境未接抽取管线：请用 CLI（ict-run）处理后再入库")


@app.post("/api/runs")
def start_run() -> None:
    raise HTTPException(501, "演示环境未接抽取管线：请用 CLI（ict-run）处理后再入库")
