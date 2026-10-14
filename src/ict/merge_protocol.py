"""Composable merge primitives. Content decisions are supplied by the model.

Compilation is transactional: invalid references or arithmetic reject the plan before any
candidate is mutated. Only the legacy, explicitly audited fallback uses the old executor.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import math

from ict.config import COB_FIELDS

PRICES = ("unit_price", "quantity", "unit", "total_price")
DESCRIPTIONS = tuple(key for key in COB_FIELDS if key not in PRICES and key != "object_name")
OPS = {"exclude", "split", "merge", "sum", "name_from", "price_from", "fields_from",
       "fields_reject", "category_from_announcement"}


class PlanConstraintError(ValueError):
    """An unsafe execution plan, distinct from malformed JSON and business uncertainty."""


class PlanSchemaError(ValueError):
    """A malformed plan representation."""


class PlanBusinessError(ValueError):
    """A supplied observation cannot support the requested value; do not re-extract JSON."""


def validate_plan_shape(parsed):
    if not isinstance(parsed.get("deltas"), list) or not isinstance(parsed.get("quantity_accounting", []), list):
        raise PlanSchemaError("需要 deltas 与 quantity_accounting 数组")
    for index, delta in enumerate(parsed["deltas"]):
        if not isinstance(delta, dict) or not isinstance(delta.get("op"), str) or delta["op"] not in OPS:
            raise PlanSchemaError(f"deltas[{index}].op 无效")
        if delta["op"] == "exclude" and delta.get("kind") not in ("subtotal_row", "project_row", "index_row", "not_an_object"):
            raise PlanSchemaError(f"deltas[{index}].kind 无效")
        for key in ("candidate_id", "source", "target", "as", "mode", "raw_item"):
            if key in delta and (not isinstance(delta[key], str) or not delta[key]):
                raise PlanSchemaError(f"deltas[{index}].{key} 必须为非空字符串")
        if "group_id" in delta and (isinstance(delta["group_id"], bool) or not isinstance(delta["group_id"], (str, int))):
            raise PlanSchemaError(f"deltas[{index}].group_id 必须为组引用")
        for key in ("keys", "sources", "candidate_ids", "members", "group_ids", "groups"):
            if key in delta and (not isinstance(delta[key], list) or not delta[key] or
                    any(isinstance(value, bool) or not isinstance(value, (str, int) if key in {"groups", "group_ids"} else str)
                        for value in delta[key])):
                raise PlanSchemaError(f"deltas[{index}].{key} 结构无效")
        if delta["op"] == "split":
            parts = delta.get("parts")
            if not isinstance(parts, list) or any(not isinstance(part, dict) or not isinstance(part.get("as"), str)
                    or not isinstance(part.get("members"), list) or any(not isinstance(cid, str) for cid in part["members"])
                    for part in parts):
                raise PlanSchemaError(f"deltas[{index}].parts 需要命名成员组")
    for index, item in enumerate(parsed.get("quantity_accounting", [])):
        if (not isinstance(item, dict) or not isinstance(item.get("candidate_id"), str) or
                not isinstance(item.get("reason"), str) or not item["reason"].strip() or
                item.get("disposition") not in ("duplicate", "alternative", "unresolved")):
            raise PlanSchemaError(f"quantity_accounting[{index}] 结构或枚举无效")
        if item.get("relates_to") is not None and not isinstance(item["relates_to"], str):
            raise PlanSchemaError(f"quantity_accounting[{index}].relates_to 必须为引用")


@dataclass
class PlanGroup:
    ref: str
    members: list[str]
    bindings: dict = field(default_factory=dict)
    rejected: set[str] = field(default_factory=set)
    entries: list[dict] = field(default_factory=list)


@dataclass
class Compiled:
    groups: dict[str, PlanGroup]
    candidates: dict
    readings: dict
    excluded: dict
    decisions: list[dict]
    accounting: list[dict]
    package_evidence: dict = field(default_factory=dict)


def group_ref(value):
    return str(value) if str(value).startswith("g:") else f"g:{value}"


def candidate_ref(value):
    return str(value)[2:] if str(value).startswith("c:") else str(value)


def compile_plan(baseline, candidates, parsed, ownership, value_of, sum_bundle,
                 package_amount=None, categories=None, package_evidence=None) -> Compiled:
    """Validate the full dependency graph and compute readings, without publishing a value."""
    validate_plan_shape(parsed)
    by_id = {key: item.model_copy(deep=True) for key, item in candidates.items()}
    live = {group_ref(group.group_id): PlanGroup(group_ref(group.group_id),
                                               [item.candidate_id for item in group.members])
            for group in baseline}
    declared = set(live)
    readings, excluded, decisions = {}, {}, []
    topology_done = False
    values_started = False
    deltas = parsed.get("deltas")
    if not isinstance(deltas, list):
        raise ValueError("需要 deltas 数组")

    def group(value):
        ref = group_ref(value)
        if ref not in live:
            raise ValueError(f"组 {ref} 不存在或已被消耗")
        return live[ref]

    def candidate(value):
        ref = candidate_ref(value)
        if ref not in by_id:
            raise ValueError(f"未知候选 {ref}")
        return ref

    def fresh(value, prefix):
        if not isinstance(value, str) or not value:
            raise ValueError("新结果必须使用非空 as 显式命名")
        ref = value if value.startswith(prefix) else prefix + value
        if ref in declared:
            raise ValueError(f"结果编号重复 {ref}")
        declared.add(ref)
        return ref

    def reading(source):
        if source in readings:
            return readings[source]
        if source == "p:amount":
            if package_amount is None or len(live) != 1:
                raise ValueError("包金额不可用，或仍有多个输出组")
            return {"bundle": {"total_price": package_amount}, "sources": [], "kind": "package"}
        cid = candidate(source)
        if not ownership(by_id[cid])[0]:
            raise ValueError(f"来源 {cid} 没有供价资格")
        return {"bundle": {key: value_of(by_id[cid], key) for key in PRICES},
                "sources": [cid], "kind": "single"}

    for index, delta in enumerate(deltas):
        if not isinstance(delta, dict) or delta.get("op") not in OPS:
            raise ValueError("未知 delta")
        op = delta["op"]
        entry = deepcopy(delta) | {"accepted": True, "applied": False, "sequence": index}
        decisions.append(entry)
        if op in {"exclude", "split", "merge"}:
            if topology_done:
                raise ValueError("拓扑操作必须先于计算与取值")
            if op == "exclude":
                ids = delta.get("candidate_ids") or [delta.get("candidate_id")]
                if not isinstance(ids, list) or not ids:
                    raise ValueError("exclude 需要 candidate_ids")
                if delta.get("kind") not in {"subtotal_row", "project_row", "index_row", "not_an_object"}:
                    raise ValueError("exclude kind 无效")
                ids = [candidate(cid) for cid in ids]
                if len(ids) != len(set(ids)) or any(cid in excluded for cid in ids):
                    raise ValueError("重复 exclude")
                for cid in ids:
                    excluded[cid] = delta.get("kind")
                    for ref, target in list(live.items()):
                        if cid in target.members:
                            target.members.remove(cid)
                            if not target.members:
                                del live[ref]
                entry["applied"] = True
            elif op == "split":
                target = group(delta.get("group_id"))
                parts = delta.get("parts")
                if not isinstance(parts, list) or len(parts) < 2:
                    raise ValueError("split 需要至少两个命名 parts")
                seen, created = [], []
                for part in parts:
                    if not isinstance(part, dict) or not isinstance(part.get("members"), list) or not part["members"]:
                        raise ValueError("split part 需要 as、members")
                    members = [candidate(cid) for cid in part["members"]]
                    seen.extend(members)
                    created.append(PlanGroup(fresh(part.get("as"), "g:"), members))
                if len(seen) != len(set(seen)) or set(seen) != set(target.members):
                    raise ValueError("split 必须无重复、无遗漏地划分原组")
                del live[target.ref]
                live.update({item.ref: item for item in created})
                entry.update(applied=True, new_group_ids=[item.ref for item in created])
            else:
                refs = delta.get("group_ids", delta.get("groups"))
                if not isinstance(refs, list) or len(refs) < 2 or len({group_ref(ref) for ref in refs}) != len(refs):
                    raise ValueError("merge 需要至少两个不同活组")
                targets = [group(ref) for ref in refs]
                target = PlanGroup(fresh(delta.get("as"), "g:"),
                                   [cid for item in targets for cid in item.members])
                for item in targets:
                    del live[item.ref]
                live[target.ref] = target
                entry.update(applied=True, new_group_id=target.ref)
            continue
        topology_done = True
        if op == "fields_reject":
            if values_started:
                raise ValueError("字段否定必须先于计算与取值")
            keys = delta.get("keys")
            if not isinstance(keys, list) or not keys or any(key not in COB_FIELDS or key == "object_name" for key in keys):
                raise ValueError("fields_reject keys 无效，不能否定名称")
            target_ref = delta.get("target")
            if isinstance(target_ref, str) and target_ref.startswith("c:"):
                row = by_id[candidate(target_ref)]
                for key in keys:
                    obs = row.fields[key]
                    obs.status, obs.normalized_value = "unsupported", None
                    obs.normalization = {**(obs.normalization or {}), "rejected_by_delta": True}
                total = row.fields["total_price"]
                if set(keys) & {"unit_price", "quantity"} and (total.normalization or {}).get("filled_from") == "unit_price*quantity":
                    total.status, total.normalized_value = "unsupported", None
                    total.normalization = {**total.normalization, "dependency_rejected": True}
            else:
                target = group(target_ref or delta.get("group_id"))
                target.rejected.update(keys)
                target.entries.append(entry)
            entry["applied"] = True
            continue
        values_started = True
        target = group(delta.get("group_id"))
        target.entries.append(entry)
        if op == "sum":
            sources = delta.get("sources", delta.get("members"))
            if not isinstance(sources, list) or len(sources) < 2:
                raise ValueError("sum 需要至少两个来源")
            leaves = []
            for source in sources:
                leaves.extend(reading(source)["sources"])
            if len(leaves) != len(set(leaves)):
                raise ValueError("sum 重复使用同一候选")
            if any(cid not in target.members and cid not in excluded for cid in leaves):
                raise ValueError("sum 不能搬走另一活组的计量观测")
            bundle, why = sum_bundle([by_id[cid] for cid in leaves])
            if bundle is None:
                raise ValueError(f"sum 不可计算: {why}")
            if any(isinstance(number, (int, float)) and not math.isfinite(number) for number in bundle.values()):
                raise ValueError("sum 产生非有限数值")
            ref = fresh(delta.get("as"), "r:")
            readings[ref] = {"bundle": bundle, "sources": leaves, "kind": "sum", "group_ref": target.ref}
            entry.update(applied=True, reading_ref=ref, computed=bundle)
        elif op in {"name_from", "fields_from"}:
            cid = candidate(delta.get("candidate_id"))
            keys = ["object_name"] if op == "name_from" else delta.get("keys")
            if not isinstance(keys, list) or not keys or any(key not in DESCRIPTIONS for key in keys if key != "object_name"):
                raise ValueError("fields_from 只能绑定描述字段")
            if op == "fields_from" and "object_name" in keys:
                raise ValueError("名称必须使用 name_from")
            mode = "select" if op == "name_from" else delta.get("mode", "fill")
            if mode not in {"fill", "select"}:
                raise ValueError("mode 只能取 fill 或 select")
            for key in keys:
                if key in target.bindings:
                    raise ValueError(f"同组字段重复绑定 {key}")
                if value_of(by_id[cid], key) in (None, ""):
                    raise PlanBusinessError(f"来源 {cid} 没有可用的 {key}")
                target.bindings[key] = {"candidate_id": cid, "mode": mode, "entry": entry}
        elif op == "price_from":
            source = delta.get("source", delta.get("candidate_id"))
            result = reading(source)
            if result.get("group_ref") not in (None, target.ref):
                raise ValueError("读法属于另一个最终组")
            if any(cid not in target.members and cid not in excluded for cid in result["sources"]):
                raise ValueError("计量来源属于另一活组")
            keys = delta.get("keys", ["total_price"] if result["kind"] == "package" else list(PRICES))
            if result["kind"] == "package" and keys != ["total_price"]:
                raise ValueError("包金额只能供 total_price")
            if "keys" not in delta and not any(isinstance(result["bundle"].get(key), (int, float))
                                               and not isinstance(result["bundle"].get(key), bool)
                                               and math.isfinite(result["bundle"][key])
                                               for key in ("unit_price", "total_price")):
                raise PlanBusinessError("target_states_no_number_price")
            if not isinstance(keys, list) or not keys or any(key not in PRICES for key in keys):
                raise ValueError("price_from keys 无效")
            if not any(result["bundle"].get(key) not in (None, "") for key in keys):
                raise PlanBusinessError("计量来源没有所请求的字段")
            for key in keys:
                if key in target.bindings:
                    raise ValueError(f"计量字段重复绑定 {key}")
                target.bindings[key] = {"reading": deepcopy(result), "source": source, "entry": entry}
            entry["bundle"] = "partial_keys" if "keys" in delta else result["kind"]
        elif op == "category_from_announcement":
            raw_item = delta.get("raw_item")
            if raw_item not in (categories or {}):
                raise ValueError("品目必须引用已给出的公告概要原文")
            hit = categories[raw_item]
            if hit.get("match_type") == "unmatched":
                raise ValueError("category_unmatched")
            entry.update({key: hit.get(key) for key in ("matched_code", "matched_name", "match_type")})
            for key, val in (("category_code", hit["matched_code"]), ("category_name", hit["matched_name"]),
                             ("category_type", hit.get("category_type"))):
                if key in target.bindings:
                    raise ValueError(f"品目字段重复绑定 {key}")
                target.bindings[key] = {"value": val, "mode": "fill", "source": raw_item, "entry": entry}
    # A later field rejection must not leave an earlier copied reading live.
    for target in live.values():
        for key, binding in target.bindings.items():
            if key in target.rejected:
                raise ValueError("同一最终字段不能同时绑定与否定")
            if "reading" in binding:
                for cid in binding["reading"]["sources"]:
                    if by_id[cid].fields[key].status == "unsupported":
                        raise ValueError("已否定字段不能继续供价")
            elif "candidate_id" in binding and value_of(by_id[binding["candidate_id"]], key) in (None, ""):
                raise ValueError("已否定字段不能继续补值")
    accounting = deepcopy(parsed.get("quantity_accounting", []))
    if not isinstance(accounting, list):
        raise ValueError("quantity_accounting 必须是数组")
    seen = set()
    for item in accounting:
        if not isinstance(item, dict):
            raise ValueError("quantity_accounting 每项必须是对象")
        cid = candidate(item.get("candidate_id"))
        keys = item.get("keys")
        if keys is not None and (not isinstance(keys, list) or not keys or
                any(key not in PRICES for key in keys) or len(set(keys)) != len(keys)):
            raise PlanSchemaError("计量解释 keys 必须为不重复的计量字段")
        covered = {(cid, key) for key in (keys or PRICES)}
        if seen & covered or item.get("disposition") not in {"duplicate", "alternative", "unresolved"}:
            raise PlanSchemaError("计量解释重复或 disposition 无效")
        seen.update(covered)
        if not item.get("reason"):
            raise ValueError("计量解释需要 reason")
        reference = item.get("relates_to")
        if reference and reference not in readings and reference not in live and candidate_ref(reference) not in by_id:
            raise ValueError("计量解释引用未知结果")
    return Compiled(live, by_id, readings, excluded, decisions, accounting, package_evidence or {})


def materialize(compiled, ownership, value_of, rank, package_no):
    """Return values plus field-level provenance; unresolved observations never disappear."""
    outputs, bindings, unresolved, conflicts = [], [], [], []
    accounted_usage = {}
    accounting = {}
    for item in compiled.accounting:
        accounting.setdefault(candidate_ref(item["candidate_id"]), []).append(item)

    def provenance(cid, key):
        row = compiled.candidates[cid]
        return {"candidate_id": cid, "field": key, "value": value_of(row, key),
                "source_refs": row.evidence.source_refs if row.evidence else []}

    def contributors(source, key):
        ids = compiled.readings.get(source, {}).get("sources", [])
        if not ids and source and candidate_ref(source) in compiled.candidates:
            ids = [candidate_ref(source)]
        result = []
        for cid in ids:
            if value_of(compiled.candidates[cid], key) in (None, ""):
                continue
            result.append(provenance(cid, key))
            normalization = compiled.candidates[cid].fields[key].normalization or {}
            if normalization.get("filled_from") == "unit_price*quantity":
                result.extend(provenance(cid, dep) for dep in ("unit_price", "quantity"))
        return result
    for target in compiled.groups.values():
        members = sorted((compiled.candidates[cid] for cid in target.members), key=rank)
        if not any(ownership(item)[1] for item in members):
            continue
        values = {key: None for key in COB_FIELDS}
        origins = {}
        # The baseline is descriptive fill within a model-approved group. Conflicts stay visible.
        for key in COB_FIELDS:
            eligible = [item for item in members if key not in PRICES or ownership(item)[0]]
            observations = [(item.candidate_id, value_of(item, key)) for item in eligible
                            if value_of(item, key) not in (None, "")]
            if observations:
                origins[key], values[key] = observations[0]
            distinct = {str(value) for _, value in observations}
            if key not in PRICES and len(distinct) > 1 and key not in target.bindings and key not in target.rejected:
                conflicts.append({"package_no": package_no, "group_id": target.ref, "field": key,
                                  "values": [value for _, value in observations]})
        # Use one complete observation for default money; never mix tuples by filling cells.
        eligible = [item for item in members if ownership(item)[0]]
        default = next((item for item in eligible if any(value_of(item, key) not in (None, "")
                                                       for key in ("unit_price", "total_price"))), None)
        if default is not None:
            for key in PRICES:
                values[key] = value_of(default, key)
                origins[key] = default.candidate_id
        else:
            values["unit_price"] = values["total_price"] = None
        for key, binding in target.bindings.items():
            mode = binding.get("mode", "select")
            if mode == "fill" and values[key] not in (None, ""):
                continue
            if "reading" in binding:
                values[key] = binding["reading"]["bundle"].get(key)
                origins[key] = binding["source"]
            elif "candidate_id" in binding:
                values[key] = value_of(compiled.candidates[binding["candidate_id"]], key)
                origins[key] = binding["candidate_id"]
            else:
                values[key], origins[key] = binding["value"], binding["source"]
            binding["entry"]["applied"] = True
            binding["entry"].setdefault("filled", []).append(key)
        for key in target.rejected:
            values[key], origins[key] = None, "explicit_null"
        for key in COB_FIELDS:
            if values[key] in (None, "") and key not in target.rejected:
                origins[key] = None
        dependencies = {key: contributors(origins.get(key), key) if values[key] not in (None, "") else []
                        for key in PRICES}
        if (values["total_price"] is not None and "total_price" not in target.bindings and
                any(dep["field"] in target.rejected for dep in dependencies["total_price"])
                and any(dep["field"] in {"unit_price", "quantity"} for dep in dependencies["total_price"])):
            values["total_price"], origins["total_price"], dependencies["total_price"] = None, None, []
        calculation = None
        if values["total_price"] is None and "total_price" not in target.rejected:
            operands = [values[key] for key in ("unit_price", "quantity")]
            if all(isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)
                   for value in operands):
                total = round(operands[0] * operands[1], 4)
                if not math.isfinite(total):
                    raise PlanConstraintError("选定字段计算产生非有限金额")
                values["total_price"] = total
                origins["total_price"] = f"calc:{target.ref}:total_price"
                dependencies["total_price"] = dependencies["unit_price"] + dependencies["quantity"]
                calculation = {"formula": "unit_price*quantity", "inputs": [
                    {"field": key, "value": values[key], "source": origins.get(key),
                     "dependencies": dependencies[key]} for key in ("unit_price", "quantity")]}
        used_fields = {(dep["candidate_id"], dep["field"]) for key in PRICES
                       for dep in dependencies[key]}
        used = {cid for cid, _ in used_fields}
        for item in eligible:
            cid = item.candidate_id
            remaining = [key for key in ("quantity", "total_price")
                         if (value_of(item, key) not in (None, "") or item.fields[key].raw_value not in (None, ""))
                         and (cid, key) not in used_fields]
            explanations = accounting.get(cid, [])
            covered = set()
            for explanation in explanations:
                # Historical responses named an entire candidate. Expand only to unused fields;
                # never interpret natural-language reasons or let empty cells count as adoption.
                keys = explanation.get("keys", remaining)
                explanation["resolved_keys"] = sorted(set(explanation.get("resolved_keys", [])) | set(keys))
                explanation["legacy_scope"] = "keys" not in explanation
                overlap = [key for key in keys if (cid, key) in used_fields]
                if overlap:
                    unresolved.append({"package_no": package_no, "group_id": target.ref,
                        "candidate_id": cid, "keys": overlap, "reason": "accounting_conflicts_with_binding"})
                if explanation["disposition"] != "unresolved":
                    covered.update(set(keys) - set(overlap))
            missing = [key for key in remaining if key not in covered]
            if missing:
                unresolved.append({"package_no": package_no, "group_id": target.ref,
                                   "candidate_id": cid, "keys": missing,
                                   "reason": "quantity_observation_unresolved"})
        for key in COB_FIELDS:
            source = origins.get(key)
            origin_row = compiled.candidates.get(candidate_ref(source)) if source else None
            norm = origin_row.fields[key].normalization if origin_row else None
            reading = compiled.readings.get(source, {})
            source_refs = list(dict.fromkeys(ref for cid in reading.get("sources", [])
                                            for ref in (compiled.candidates[cid].evidence.source_refs
                                                        if compiled.candidates[cid].evidence else [])))
            bindings.append({"package_no": package_no, "group_id": target.ref, "field": key,
                             "source": source, "value": values[key],
                             "requested_source": target.bindings.get(key, {}).get("source",
                                 target.bindings.get(key, {}).get("candidate_id")),
                             "method": "rejected" if key in target.rejected else
                             "calculated" if key == "total_price" and calculation else
                             "calculated" if str(source).startswith("r:") or (norm or {}).get("filled_from") else
                             "selected" if key in target.bindings else "observed",
                             "forbid_inference": key in target.rejected,
                             "source_refs": list(dict.fromkeys(ref for dep in dependencies.get(key, [])
                                                               for ref in dep["source_refs"])) or
                                             (origin_row.evidence.source_refs if origin_row and origin_row.evidence else source_refs),
                             "source_candidate_ids": list(dict.fromkeys(dep["candidate_id"] for dep in dependencies.get(key, []))) or
                                                     reading.get("sources", [origin_row.candidate_id] if origin_row else []),
                             "dependencies": dependencies.get(key, []),
                             **({"amount_evidence": compiled.package_evidence,
                                 "source_refs": compiled.package_evidence.get("source_refs", [])}
                                if source == "p:amount" else {}),
                             **({"calculation": calculation} if key == "total_price" and calculation else {})})
        if not values["object_name"]:
            unresolved.append({"package_no": package_no, "group_id": target.ref,
                               "candidate_id": members[0].candidate_id, "reason": "missing_object_name"})
            continue
        for contribution in used_fields:
            if contribution[1] not in {"quantity", "total_price"}:
                continue
            if contribution in accounted_usage and accounted_usage[contribution] != target.ref:
                raise PlanConstraintError("同一来源计量字段不能计入多个最终组")
            accounted_usage[contribution] = target.ref
        outputs.append({"group_ref": target.ref, "values": values, "base": members[0],
                        "members": members, "price_sources": sorted(used),
                        "price_ref": next((target.bindings[key].get("source") for key in PRICES
                                           if key in target.bindings), None)})
    for item in compiled.accounting:
        compiled.decisions.append({"package_no": package_no, "op": "quantity_accounting",
                                   "accepted": True, "applied": True, **item})
    for entry in compiled.decisions:
        if entry.get("op") in {"fields_from", "category_from_announcement", "name_from"}:
            entry["filled"] = sorted(set(entry.get("filled", [])))
    return outputs, bindings, unresolved, conflicts
