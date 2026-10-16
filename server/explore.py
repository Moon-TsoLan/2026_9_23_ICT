"""Read-only, paged graph neighborhoods independent of overview sampling."""
from fastapi import HTTPException

from .tenant import current_tenant
from .db import q as db_q
from .results import annotate_units, display_unit


def q(sql, params=()):
    return annotate_units(db_q(sql, params), db_q)


def data_version():
    rows = q("SELECT count(*) AS n, max(created_at) AS ts FROM announcement")
    row = rows[0] if rows else {}
    stamp = row.get("ts")
    return f"{row.get('n', 0)}:{stamp.isoformat() if stamp else ''}:{current_tenant()}"


def require_version(expected):
    current = data_version()
    if expected and current != expected:
        raise HTTPException(409, "资料库已更新，请刷新星图后继续补载")
    return current


def project_node(row):
    return {"id": "proj:" + row["project_id"],
            "label": f"{row['project_name']} · {display_unit(row.get('package_label'), row['package_no'])}",
            "kind": "project", "weight": float(row.get("package_total_amount") or 0)}


def company_node(row):
    return {"id": "sup:" + row["name"], "label": row["name"],
            "kind": "winner" if row.get("has_win") else "bidder", "weight": row.get("bids", 0)}


def neighborhood(party_id, offset=0, limit=30, expected_version=None):
    version = require_version(expected_version)
    kind, sep, name = party_id.partition(":")
    if not sep or not name or kind not in {"sup", "vendor", "brand", "buyer", "proj"}:
        raise HTTPException(422, "无效主体编号")
    nodes, edges = {}, []
    if kind in {"sup", "vendor"}:
        found = q("""/* explore:company */
            SELECT s.name, count(b.project_id) AS bids, coalesce(bool_or(b.is_winner), false) AS has_win
            FROM supplier s LEFT JOIN bid b ON b.supplier_id=s.supplier_id
            WHERE s.norm_name=norm_name(%s) GROUP BY s.name""", (name,))
        if found:
            target = company_node(found[0])
            if not found[0].get("bids"):
                target["kind"] = "vendor"
            name = found[0]["name"]
            kind = "sup"
        else:
            target = {"id": "vendor:" + name, "label": name, "kind": "vendor", "weight": 0,
                      "identity_kind": "company"}
            kind = "vendor"
    elif kind == "brand":
        target = {"id": party_id, "label": name, "kind": "vendor", "weight": 0,
                  "identity_kind": "brand", "roles": ["品牌替代"]}
    elif kind == "buyer":
        target = {"id": party_id, "label": name, "kind": "buyer", "weight": 0}
    else:
        found = q("/* explore:project */ SELECT * FROM project WHERE project_id=%s", (name,))
        if not found:
            raise HTTPException(404, "项目不存在")
        project = found[0]
        target = project_node(project)
    nodes[target["id"]] = target
    if kind == "proj":
        # Paginate identities, not individual bid/supply edges, so one entity's roles stay together.
        rows = q("""/* explore:project_parties */
            SELECT 'sup:'||s.name AS id, s.name, 'company' AS identity_kind,
                   CASE WHEN EXISTS(SELECT 1 FROM bid z WHERE z.supplier_id=s.supplier_id AND z.is_winner)
                        THEN 'winner' ELSE 'bidder' END AS kind,
                   CASE WHEN b.is_winner THEN 'win' ELSE 'bid' END AS role
            FROM bid b JOIN supplier s ON s.supplier_id=b.supplier_id WHERE b.project_id=%s
            UNION
            SELECT CASE WHEN nullif(c.product_supplier,'') IS NULL THEN 'brand:'||c.brand
                        WHEN s.supplier_id IS NOT NULL THEN 'sup:'||s.name ELSE 'vendor:'||c.product_supplier END,
                   coalesce(s.name, nullif(c.product_supplier,''), c.brand),
                   CASE WHEN nullif(c.product_supplier,'') IS NULL THEN 'brand' ELSE 'company' END,
                   CASE WHEN EXISTS(SELECT 1 FROM bid z WHERE z.supplier_id=s.supplier_id AND z.is_winner) THEN 'winner'
                        WHEN EXISTS(SELECT 1 FROM bid z WHERE z.supplier_id=s.supplier_id) THEN 'bidder' ELSE 'vendor' END,
                   'supply'
            FROM cob c LEFT JOIN supplier s ON s.norm_name=norm_name(nullif(c.product_supplier,''))
            WHERE c.project_id=%s AND coalesce(nullif(c.product_supplier,''),nullif(c.brand,'')) IS NOT NULL
            ORDER BY id, role""", (name, name))
        ids = list(dict.fromkeys(row["id"] for row in rows))
        total = len(ids)
        selected = set(ids[offset:offset + limit])
        for row in rows:
            if row["id"] not in selected:
                continue
            nodes[row["id"]] = {"id": row["id"], "label": row["name"], "kind": row["kind"],
                                 "weight": 1, "identity_kind": row["identity_kind"]}
            edges.append({"a": target["id"], "b": row["id"], "role": row["role"], "weight": 1})
        if project.get("purchaser"):
            buyer_id = "buyer:" + project["purchaser"]
            nodes[buyer_id] = {"id": buyer_id, "label": project["purchaser"], "kind": "buyer", "weight": 1}
            edges.append({"a": target["id"], "b": buyer_id, "role": "buy", "weight": 1})
    else:
        if kind == "sup":
            predicate = """(EXISTS(SELECT 1 FROM bid b JOIN supplier s ON s.supplier_id=b.supplier_id
                WHERE b.project_id=p.project_id AND s.name=%s)
                OR EXISTS(SELECT 1 FROM cob c WHERE c.project_id=p.project_id
                          AND norm_name(c.product_supplier)=norm_name(%s)))"""
            subject_params = (name, name)
        elif kind == "buyer":
            predicate = "p.purchaser=%s"
        elif kind == "brand":
            predicate = "EXISTS(SELECT 1 FROM cob c WHERE c.project_id=p.project_id AND nullif(c.product_supplier,'') IS NULL AND c.brand=%s)"
        else:
            predicate = "EXISTS(SELECT 1 FROM cob c WHERE c.project_id=p.project_id AND c.product_supplier=%s)"
        if kind != "sup":
            subject_params = (name,)
        totals = q(f"/* explore:total */ SELECT count(*) AS n FROM project p WHERE {predicate}", subject_params)
        total = totals[0]["n"] if totals else 0
        if not total and kind != "sup":
            raise HTTPException(404, "主体不存在")
        rows = q(f"""/* explore:projects */ SELECT p.* FROM project p WHERE {predicate}
                      ORDER BY p.project_id LIMIT %s OFFSET %s""", (*subject_params, limit, offset))
        roles = {}
        if kind == "sup" and rows:
            project_ids = [r["project_id"] for r in rows]
            observed = q("""/* explore:roles */ SELECT b.project_id,
                CASE WHEN b.is_winner THEN 'win' ELSE 'bid' END AS role
                FROM bid b JOIN supplier s ON s.supplier_id=b.supplier_id
                WHERE s.name=%s AND b.project_id=ANY(%s)
                UNION SELECT c.project_id, 'supply' FROM cob c
                WHERE norm_name(c.product_supplier)=norm_name(%s) AND c.project_id=ANY(%s)""",
                (name, project_ids, name, project_ids))
            for observed_role in observed:
                roles.setdefault(observed_role["project_id"], set()).add(observed_role["role"])
        for row in rows:
            node = project_node(row)
            nodes[node["id"]] = node
            row_roles = roles.get(row["project_id"], set()) if kind == "sup" else {"buy" if kind == "buyer" else "supply"}
            for role in sorted(row_roles):
                edges.append({"a": node["id"], "b": target["id"], "role": role, "weight": 1})
    require_version(version)
    return {"node": target, "data_version": version, "graph": {"nodes": list(nodes.values()), "edges": edges},
            "pagination": {"offset": offset, "limit": limit, "total": total, "has_more": offset + limit < total}}


def supply_search(keyword):
    rows = q("""/* explore:supply_search */
        SELECT DISTINCT CASE WHEN s.supplier_id IS NULL THEN 'vendor:'||c.product_supplier ELSE 'sup:'||s.name END AS id,
               coalesce(s.name,c.product_supplier) AS name, 'company' AS identity_kind,
               CASE WHEN EXISTS(SELECT 1 FROM bid b WHERE b.supplier_id=s.supplier_id AND b.is_winner) THEN 'winner'
                    WHEN EXISTS(SELECT 1 FROM bid b WHERE b.supplier_id=s.supplier_id) THEN 'bidder' ELSE 'vendor' END AS kind
        FROM cob c LEFT JOIN supplier s ON s.norm_name=norm_name(c.product_supplier)
        WHERE nullif(c.product_supplier,'') IS NOT NULL AND c.product_supplier ILIKE %s ORDER BY name LIMIT 8""", (f"%{keyword}%",))
    rows.extend(q("""/* explore:brand_search */ SELECT DISTINCT 'brand:'||brand AS id, brand AS name,
        'brand' AS identity_kind, 'vendor' AS kind FROM cob
        WHERE nullif(product_supplier,'') IS NULL AND nullif(brand,'') IS NOT NULL AND brand ILIKE %s
        ORDER BY name LIMIT 8""", (f"%{keyword}%",)))
    return rows
