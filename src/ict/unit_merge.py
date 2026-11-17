"""Decide procurement-unit identity before the existing within-unit object merge."""
from copy import deepcopy
import json

from ict.llm import complete_json
from ict.config import MERGE_THINKING


def decision_context(tree):
    units = {u["package_no"]: u for u in tree["units"]}
    units.update({u["ref"]: u for u in tree["virtual_units"]})
    candidates = {c["candidate_id"]: c for c in tree["candidates"]}
    sources = {ref: row["text"] for ref, row in tree["html_sources"].items()
               if ref not in ("html:body", "html:summary") and row.get("text")}
    for f in tree["files"]:
        if f["route"] == "direct":
            for p in f["parts"]:
                sources[f"{f['path']}#{p['number']}"] = p["text"]
    branches = []
    for ref, unit in units.items():
        observations = []
        for cid in unit["candidate_ids"]:
            c = candidates[cid]
            observations.append({"id": cid, "entity_type": c["entity_type"],
                                 "fields": {k: v["raw_value"] for k, v in c["fields"].items()
                                            if v.get("raw_value") is not None},
                                 "evidence": c.get("evidence")})
        branches.append({"ref": ref, "virtual": "ref" in unit, "name": unit.get("package_name"),
                         "scope_evidence": unit.get("package_evidence_text"),
                         "type": unit.get("package_kind"), "number": unit.get("source_package_no"),
                         "observations": observations})
    return {"project_name": tree["identity"].get("project_name"), "branches": branches, "sources": sources}


def validate_decisions(answer, context):
    raw = answer.get("decisions")
    branches = {b["ref"]: b for b in context["branches"]}
    if not isinstance(raw, list) or any(not isinstance(item, dict) for item in raw):
        return "decisions 必须是数组"
    items = {item["source"]: item for item in raw if isinstance(item.get("source"), str)}
    if set(items) != set(branches) or len(raw) != len(branches):
        return "每个输入分支只可出现一次，且必须覆盖全部"
    for ref, item in items.items():
        target = item.get("target")
        if "target" not in item or not isinstance(item.get("reason"), str) or not item["reason"].strip():
            return "缺少 target 或理由"
        refs = item.get("source_refs")
        if not isinstance(refs, list):
            return f"{ref} 的 source_refs 需要逐条列出"
        for r in refs:
            if not isinstance(r, str) or r not in context["sources"]:
                return f"{ref} 的证据 {r!r} 无法定位。逐项照抄 sources 的键，连续多页分别列项。"
        if target is None and branches[ref]["virtual"]:
            continue
        if not isinstance(target, str) or target not in branches or branches[target]["virtual"]:
            return "目标必须是已有真实采购单元"
        if items[target].get("target") != target:
            return "目标须是本次最终保留的单元"
        if target != ref and not refs:
            return "实际合并必须给出原文证据"
        if not branches[ref]["virtual"] and target != ref:
            kinds = {branches[k]["type"] for k in (target, ref)}
            if "lot" in kinds and "contract_package" in kinds:
                return "原文标段与合同包不能混同"
    return ""


def apply_unit_decisions(tree, answer, context):
    error = validate_decisions(answer, context)
    if error:
        raise ValueError(error)
    result = deepcopy(tree)
    units = {u["package_no"]: u for u in result["units"]}
    virtual = {u["ref"]: u for u in result["virtual_units"]}
    candidates = {c["candidate_id"]: c for c in result["candidates"]}
    for item in answer["decisions"]:
        ref, target = item["source"], item["target"]
        if target == ref or target is None:
            continue
        source = virtual.pop(ref) if ref in virtual else units.pop(ref)
        destination = units[target]
        if "package_no" in source:
            for key in ("source_package_no", "package_name", "title", "package_amount"):
                if not destination.get(key):
                    destination[key] = source.get(key)
            if source.get("package_amount") and source["package_amount"] != destination.get("package_amount"):
                destination.setdefault("amount_alternatives", []).append(source["package_amount"])
            destination.setdefault("amount_alternatives", []).extend(source.get("amount_alternatives", []))
        for cid in source["candidate_ids"]:
            candidates[cid].update(project_id=destination["project_id"], package_no=target)
            destination["candidate_ids"].append(cid)
    result["units"] = list(units.values())
    result["virtual_units"] = list(virtual.values())
    result["unit_decisions"] = answer["decisions"]
    return result


def merge_units(tree, llm):
    if not tree["units"] or (not tree["virtual_units"] and len(tree["units"]) == 1):
        return deepcopy(tree)
    if llm is None:
        raise ValueError("采购单元待消歧，需要配置模型")
    context = decision_context(tree)
    # Use source names in the model's decision, keeping internal IDs for execution.
    # This also makes two different ordinals (candidate order and lot number) unambiguous.
    labels = {}
    for branch in context["branches"]:
        name = branch["name"] or " / ".join(str(o["fields"].get("supplier_name") or
                                               o["fields"].get("object_name") or o["id"])
                                            for o in branch["observations"])
        label = f"{name} [{branch['ref']}]"
        labels[label] = branch["ref"]
    model_context = deepcopy(context)
    for branch, label in zip(model_context["branches"], labels):
        branch["ref"] = label

    def decode(obj):
        if not isinstance(obj.get("decisions"), list):
            return {}
        return {"decisions": [dict(row, source=labels.get(str(row.get("source")), ""),
                                    target=(None if row.get("target", "") is None else
                                            labels.get(str(row.get("target")), "")))
                              if isinstance(row, dict) else row for row in obj["decisions"]]}

    answer = complete_json(llm, step="merge_units", prompt_version="merge-units-v1",
                           user=json.dumps(model_context, ensure_ascii=False), thinking=MERGE_THINKING,
                           validate=lambda obj: validate_decisions(decode(obj), context))
    answer = decode(answer)
    return apply_unit_decisions(tree, answer, context)

