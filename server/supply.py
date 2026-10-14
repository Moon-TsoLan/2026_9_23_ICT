"""S1 identity and accounting: one company may hold two roles; a brand is distinct."""

S1_SQL = """
WITH win AS (
  SELECT DISTINCT p.project_id, s.name AS subject, 'company' AS identity_kind,
         '中标供应商' AS role, p.package_total_amount AS award_amount, NULL::numeric AS supply_amount
  FROM project p JOIN bid b ON b.project_id = p.project_id AND b.is_winner
  JOIN supplier s ON s.supplier_id = b.supplier_id WHERE p.purchaser = %s
), supply AS (
  SELECT c.project_id,
         CASE WHEN nullif(c.product_supplier, '') IS NOT NULL
              THEN coalesce(s.name, c.product_supplier) ELSE c.brand END AS subject,
         CASE WHEN nullif(c.product_supplier, '') IS NOT NULL THEN 'company' ELSE 'brand' END AS identity_kind,
         CASE WHEN nullif(c.product_supplier, '') IS NOT NULL THEN '产品供应商' ELSE '品牌替代' END AS role,
         NULL::numeric AS award_amount, sum(c.total_price) AS supply_amount
  FROM project p JOIN cob c ON c.project_id = p.project_id
  LEFT JOIN supplier s ON s.norm_name = norm_name(c.product_supplier)
  WHERE p.purchaser = %s AND coalesce(nullif(c.product_supplier, ''), nullif(c.brand, '')) IS NOT NULL
  GROUP BY c.project_id, subject, identity_kind, role
), links AS (SELECT * FROM win UNION ALL SELECT * FROM supply)
SELECT subject, identity_kind, string_agg(DISTINCT role, '、' ORDER BY role) AS role,
       count(DISTINCT project_id) AS coop_times, sum(award_amount) AS award_amount,
       sum(supply_amount) AS supply_amount
FROM links GROUP BY subject, identity_kind
ORDER BY coop_times DESC, identity_kind, subject
"""

SUPPLY_PAIRS_SQL = """
SELECT DISTINCT c.project_id,
       CASE WHEN nullif(c.product_supplier, '') IS NOT NULL
            THEN coalesce(s.name, c.product_supplier) ELSE c.brand END AS subject,
       CASE WHEN nullif(c.product_supplier, '') IS NOT NULL THEN 'company' ELSE 'brand' END AS identity_kind
FROM cob c LEFT JOIN supplier s ON s.norm_name = norm_name(c.product_supplier)
WHERE c.project_id = ANY(%s)
  AND coalesce(nullif(c.product_supplier, ''), nullif(c.brand, '')) IS NOT NULL
"""


def supply_id(subject, identity_kind="company"):
    return f"{'brand' if identity_kind == 'brand' else 'sup'}:{subject}"


def supply_graph(purchaser, projects, winners, supplies, rows):
    nodes = {f"buyer:{purchaser}": {"id": f"buyer:{purchaser}", "label": purchaser,
                                   "kind": "buyer", "weight": len(projects)}}
    edges = []
    metrics = {(row["subject"], row["identity_kind"]): row for row in rows}

    def subject(name, identity_kind, role):
        ref = supply_id(name, identity_kind)
        if ref not in nodes:
            row = metrics.get((name, identity_kind), {})
            nodes[ref] = {"id": ref, "label": name + ("（品牌）" if identity_kind == "brand" else ""),
                          "kind": "winner" if role == "中标供应商" else "vendor",
                          "identity_kind": identity_kind, "roles": [], "weight": row.get("coop_times", 1)}
        if role not in nodes[ref]["roles"]:
            nodes[ref]["roles"].append(role)
        return ref

    for project in projects:
        ref = f"proj:{project['project_id']}"
        nodes[ref] = {"id": ref, "label": f"{project['project_name']} · {project.get('package_label') or '采购单元 ' + project['package_no']}",
                      "kind": "project", "weight": float(project.get("package_total_amount") or 0)}
        edges.append({"a": ref, "b": f"buyer:{purchaser}", "role": "buy", "weight": 1})
    for winner in winners:
        ref = subject(winner["name"], "company", "中标供应商")
        edges.append({"a": f"proj:{winner['project_id']}", "b": ref, "role": "win", "weight": 1})
    supply_edges = {}
    for supply in supplies:
        ref = subject(supply["subject"], supply["identity_kind"],
                      "品牌替代" if supply["identity_kind"] == "brand" else "产品供应商")
        edge = supply_edges.setdefault(ref, {"a": ref, "b": f"buyer:{purchaser}",
                                             "role": "supply", "weight": 0, "project_ids": []})
        if supply["project_id"] not in edge["project_ids"]:
            edge["project_ids"].append(supply["project_id"])
            edge["weight"] += 1
    edges.extend(supply_edges.values())
    return list(nodes.values()), edges
