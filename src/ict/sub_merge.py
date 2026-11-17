"""Small per-package SUB identity/role adjudication using references to observed values."""
import json

from ict.llm import complete_json, LLMError, ValidationIssue
from ict.schemas import Sub
from ict.config import SUB_FIELDS


def merge_subs(items, package_no, llm, counter, value_of, package_metadata=None, source_contexts=None):
    rows = [item for item in items if item.entity_type == "sub"]
    if llm is None or len(rows) < 2:
        return None
    by_id = {item.candidate_id: item for item in rows}
    payload = {"package_no": package_no, "package_registry": [package_metadata] if package_metadata else [], "candidates": [
        {"candidate_id": item.candidate_id,
         "fields": {key: value_of(item, key) for key in
                    SUB_FIELDS},
         "evidence": item.evidence.model_dump() if item.evidence else None,
         "unmapped_fields": item.unmapped_fields} for item in rows]}
    refs = {ref for item in rows if item.evidence for ref in item.evidence.source_refs}
    payload["source_contexts"] = {ref: context for ref, context in (source_contexts or {}).items() if ref in refs}

    def validate(parsed):
        groups, excluded = parsed.get("groups"), parsed.get("excluded", [])
        if not isinstance(groups, list) or not isinstance(excluded, list):
            return "需要 groups、excluded 数组"
        seen = []
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("candidate_ids"), list) or not group["candidate_ids"]:
                return "每组需要 candidate_ids"
            ids = group["candidate_ids"]
            if any(not isinstance(cid, str) or cid not in by_id for cid in ids):
                return ValidationIssue("SUB 组引用未知候选", "plan_constraint")
            seen.extend(ids)
            if group.get("name_from") not in ids or not value_of(by_id[group["name_from"]], "supplier_name"):
                return ValidationIssue("name_from 必须引用本组有名称的候选", "plan_constraint")
            if not isinstance(group.get("winner"), bool) or not isinstance(group.get("reason"), str) or not group["reason"].strip():
                return "每组需要 winner 布尔值和 reason"
            for key in ("score_from", "amount_from", "bid_amount_from", "review_from", "review_reason_from"):
                if group.get(key) is not None and (not isinstance(group[key], str) or group[key] not in ids):
                    return ValidationIssue(f"{key} 必须引用本组候选", "plan_constraint")
            review_source = group.get("review_from")
            review = value_of(by_id[review_source], "review_status") if review_source else "unknown"
            if review not in (None, "", "unknown", "passed", "failed"):
                return ValidationIssue("审查状态不可解析", "plan_constraint")
            if review == "failed" and group["winner"]:
                return ValidationIssue("未通过审查不能同时确认中标，需结合证据解决冲突", "business")
            if group.get("review_reason_from") and review != "failed":
                return ValidationIssue("未通过原因只能跟随 failed 结论", "plan_constraint")
            # Every populated new field requires either a source or an explicit unresolved note.
            unresolved_fields = group.get("unresolved_fields", [])
            if not isinstance(unresolved_fields, list) or any(field not in SUB_FIELDS for field in unresolved_fields):
                return "unresolved_fields 必须为业务字段名数组"
            for field, selector in (("bid_amount", "bid_amount_from"), ("review_status", "review_from"),
                                    ("review_failure_reason", "review_reason_from")):
                selected = group.get(selector)
                if selected and value_of(by_id[selected], field) in (None, "", "unknown"):
                    return ValidationIssue(f"{selector} 指向不可用观测", "plan_constraint")
                if any(value_of(by_id[cid], field) not in (None, "", "unknown") for cid in ids):
                    if group.get(selector) is None and field not in unresolved_fields:
                        return ValidationIssue(f"{field} 已有观测，必须选来源或明确未决", "plan_constraint")
        for entry in excluded:
            if (not isinstance(entry, dict) or not isinstance(entry.get("candidate_id"), str)
                    or entry["candidate_id"] not in by_id or not isinstance(entry.get("reason"), str)
                    or not entry["reason"].strip()):
                return "excluded 需要有效 candidate_id 与 reason"
            if value_of(by_id[entry["candidate_id"]], "review_status") == "failed":
                return ValidationIssue("未通过投标主体必须保留在供应商分组", "plan_constraint")
            seen.append(entry["candidate_id"])
        if len(seen) != len(by_id) or set(seen) != set(by_id):
            return ValidationIssue("SUB 计划必须无重复且完整覆盖候选", "plan_constraint")
        return ""

    try:
        answer = complete_json(llm, step="merge_subs", prompt_version="merge-subs-v3",
                               user=json.dumps(payload, ensure_ascii=False), validate=validate, counter=counter)
    except (ValueError, LLMError) as exc:
        return {"fallback": str(exc), "attempts": getattr(exc, "attempts", []), "accepted": False}
    output, source_ids, unresolved = [], [], []
    for group in answer["groups"]:
        values = {}
        for field, selector in (("supplier_name", "name_from"), ("score", "score_from"),
                                ("supplier_award_amount", "amount_from"), ("bid_amount", "bid_amount_from"),
                                ("review_status", "review_from"), ("review_failure_reason", "review_reason_from")):
            source = group.get(selector)
            values[field] = value_of(by_id[source], field) if source else None
            if source and values[field] in (None, ""):
                unresolved.append({"package_no": package_no, "candidate_id": source,
                                   "field": field, "reason": "sub_selected_field_unavailable"})
        values["review_status"] = values["review_status"] or "unknown"
        for field in group.get("unresolved_fields", []):
            unresolved.append({"package_no": package_no, "field": field, "reason": group["reason"],
                               "candidate_ids": group["candidate_ids"]})
        output.append(Sub(**values, is_winner=group["winner"]))
        source_ids.append(group["name_from"])
    return {"accepted": True, "subs": output, "source_ids": source_ids,
            "unresolved": unresolved, "groups": answer["groups"], "excluded": answer.get("excluded", [])}
