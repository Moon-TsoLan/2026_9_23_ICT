"""Single read adapter for current results and historical relational records."""
from __future__ import annotations

from copy import deepcopy
from ict.packages import package_context, package_label


def result_from_storage(aid, raw, query):
    raw = raw or {}
    if raw.get("official_result"):
        return deepcopy(raw["official_result"])
    projects = query("SELECT * FROM project WHERE announcement_id = %s ORDER BY project_id", (aid,))
    ids = [p["project_id"] for p in projects]
    cobs = query("SELECT * FROM cob WHERE project_id = ANY(%s) ORDER BY cob_id", (ids,)) if ids else []
    bids = query("""SELECT b.*, s.name AS supplier_name FROM bid b JOIN supplier s USING (supplier_id)
                    WHERE b.project_id = ANY(%s) ORDER BY b.project_id, s.name""", (ids,)) if ids else []
    understood = (raw.get("package_bindings") or {}).get("effective_understanding", {})
    known = {p["package_no"]: p for p in understood.get("packages", [])}
    packages = []
    for p in projects:
        package = dict(p)
        package.update(package_context(known.get(p["package_no"], {"package_no": p["package_no"]})))
        package["cobs"] = [dict(c) for c in cobs if c["project_id"] == p["project_id"]]
        original = raw.get("sub_results", {}).get(str(p["package_no"]))
        package["subs"] = deepcopy(original) if original is not None else [
            dict(b) for b in bids if b["project_id"] == p["project_id"]]
        packages.append(package)
    return {"schema_version": "announcement-v2", "announcement_id": aid, "packages": packages}


def annotate_units(rows, query):
    ids = list(dict.fromkeys(r["project_id"] for r in rows if "project_id" in r and "package_no" in r))
    if not ids:
        return rows
    stored = query("""SELECT p.project_id, p.package_no, a.raw_json FROM project p
                      JOIN announcement a USING (announcement_id) WHERE p.project_id = ANY(%s)""", (ids,))
    metadata = {}
    for p in stored:
        raw = p.get("raw_json") or {}
        ref = raw.get("project_map", {}).get(p["project_id"], p["package_no"])
        result = raw.get("official_result") or (raw.get("package_bindings") or {}).get("effective_understanding", {})
        unit = next((v for v in result.get("packages", []) if v.get("package_no") == ref), {})
        metadata[p["project_id"]] = {**package_context(unit), "package_label": package_label(unit)}
    for row in rows:
        if row.get("project_id") in metadata:
            row.update({k: v for k, v in metadata[row["project_id"]].items() if k != "package_no"})
    return rows


def display_unit(name, number):
    return name or ("采购单元 " + str(number))
