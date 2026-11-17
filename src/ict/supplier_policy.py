"""The two agreed final SUB defaults; observed candidates remain unchanged."""
from __future__ import annotations


def supplier_defaults(sub, blocked=()):
    """Return policy additions only. Explicit observations and unresolved fields win."""
    get = sub.get if isinstance(sub, dict) else lambda key: getattr(sub, key)
    changes = {}
    if ("bid_amount" not in blocked and get("bid_amount") is None and get("is_winner") is True
            and get("supplier_award_amount") is not None):
        changes["bid_amount"] = get("supplier_award_amount")
    if "review_status" not in blocked and get("review_status") in (None, "", "unknown"):
        changes["review_status"] = "passed"
    return changes


def finalize_suppliers(merged):
    """Apply once after merge/repair; record additions in the existing field audit."""
    for project in merged.projects:
        decisions = [item for item in merged.sub_merge_decisions if item.get("package_no") == project.package_no]
        groups = [group for decision in decisions if decision.get("accepted") for group in decision.get("groups", [])]
        sources = (project.provenance or {}).get("sub_candidate_ids", [])
        for index, sub in enumerate(project.subs):
            source = sources[index] if index < len(sources) else None
            members = next((set(group["candidate_ids"]) for group in groups
                            if group.get("name_from") == source), {source})
            blocked = {item["field"] for item in merged.unresolved + merged.conflicts
                       if item.get("package_no", project.package_no) == project.package_no and item.get("field")
                       and (item.get("supplier_name") == sub.supplier_name
                            or item.get("candidate_id") in members
                            or members.intersection(item.get("candidate_ids", [])))}
            for field, value in supplier_defaults(sub, blocked).items():
                setattr(sub, field, value)
                merged.field_bindings.append({"package_no": project.package_no,
                    "supplier_name": sub.supplier_name, "field": field, "value": value,
                    "method": "supplier_policy", "source": "supplier_award_amount" if field == "bid_amount"
                    else "no_explicit_review_failure"})
