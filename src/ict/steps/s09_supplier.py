"""Post-merge self-supply inference. Never infer without an explicit model decision."""
from __future__ import annotations

import json

from ict.concurrency import parallel_map
from ict.llm import LLMError, complete_json
from ict.schemas import Failure
from ict.packages import package_context


def complete_suppliers(merged, llm, counter=None):
    failures = []

    def one(project):
        eligible = {}
        for index, cob in enumerate(project.cobs):
            forbidden = any(binding.get("package_no") == project.package_no
                            and binding.get("cob_index") == index
                            and binding.get("field") == "product_supplier"
                            and binding.get("forbid_inference") for binding in merged.field_bindings)
            if not cob.brand and not cob.product_supplier and not forbidden:
                eligible[f"{project.project_id}:cob:{index}"] = index
        winners = {f"winner:{index}": sub.supplier_name for index, sub in enumerate(project.subs) if sub.is_winner}
        if not eligible or not winners or llm is None:
            return [], []
        payload = {"project_name": project.project_name, **package_context(project),
                   "winner_suppliers": [{"winner_ref": ref, "supplier_name": name} for ref, name in winners.items()],
                   "final_cobs": [{"cob_ref": ref, **project.cobs[index].model_dump()} for ref, index in eligible.items()],
                   "field_bindings": [item for item in merged.field_bindings if item.get("package_no") == project.package_no]}

        def validate(parsed):
            decisions = parsed.get("decisions")
            if not isinstance(decisions, list) or any(not isinstance(item, dict) for item in decisions):
                return "需要 decisions 数组"
            if (any(not isinstance(item.get("cob_ref"), str) for item in decisions)
                    or len(decisions) != len(eligible) or {item.get("cob_ref") for item in decisions} != set(eligible)):
                return "逐一覆盖所有待补 COB，不得重复或遗漏"
            for decision in decisions:
                if (not isinstance(decision.get("decision"), str)
                        or decision.get("decision") not in {"confirm", "reject", "uncertain"}
                        or not isinstance(decision.get("reason"), str) or not decision["reason"].strip()):
                    return "decision 必须为 confirm/reject/uncertain，并写 reason"
                if decision.get("decision") == "confirm" and (
                        not isinstance(decision.get("winner_ref"), str) or decision.get("winner_ref") not in winners):
                    return "确认自供只能引用本包提供的 winner_ref"
            return ""

        try:
            result = complete_json(llm, step="complete_suppliers", prompt_version="supplier-completion-v1",
                                   user=json.dumps(payload, ensure_ascii=False), validate=validate, counter=counter)
        except (LLMError, ValueError) as exc:
            return ([{"package_no": project.package_no, "decision": "failed", "reason": str(exc)}],
                    [Failure(failure_code="llm_schema_invalid", location=project.package_no, failure_message=str(exc))])
        audit = []
        for decision in result["decisions"]:
            index = eligible[decision["cob_ref"]]
            entry = {"package_no": project.package_no, "cob_index": index, **decision, "applied": False}
            if decision["decision"] == "confirm":
                name = winners[decision["winner_ref"]]
                project.cobs[index].product_supplier = name
                entry.update(applied=True, value=name, method="inferred_winner_self_supply")
                merged_binding = {"package_no": project.package_no, "cob_index": index,
                                  "field": "product_supplier", "value": name, "method": "inferred",
                                  "source": decision["winner_ref"], "reason": decision["reason"]}
                entry["field_binding"] = merged_binding
            audit.append(entry)
        return audit, []

    for audit, errors in parallel_map(one, merged.projects):
        merged.supplier_inferences.extend(audit)
        merged.field_bindings.extend(item["field_binding"] for item in audit if "field_binding" in item)
        failures.extend(errors)
    for project in merged.projects:
        products = list(dict.fromkeys(cob.product_supplier for cob in project.cobs if cob.product_supplier))
        for sub in project.subs:
            sub.cooperative_product_suppliers = products if sub.is_winner else []
    return failures
