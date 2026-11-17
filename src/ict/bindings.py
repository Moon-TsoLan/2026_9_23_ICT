"""One model authority for package ownership, reused when attachment evidence arrives."""
from __future__ import annotations

import json

from ict.evidence import html_rows
from ict.ids import make_project_id
from ict.llm import LLMError, LLMValidationError, complete_json
from ict.money import parse_price_cell
from ict.packages import hint_reference
from ict.schemas import Amount, Failure


def winner_names(candidates) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for candidate in candidates:
        if candidate.entity_type != "sub" or not candidate.package_no:
            continue
        winner = candidate.fields["is_winner"]
        value = winner.normalized_value if winner.normalized_value is not None else winner.raw_value
        if value not in (True, "true", "True", "是", "中标", "1"):
            continue
        name = candidate.fields["supplier_name"].normalized_value or candidate.fields["supplier_name"].raw_value
        if name and name not in result.setdefault(candidate.package_no, []):
            result[candidate.package_no].append(str(name))
    return result


def bind_packages(understanding, candidates, notice, tables, llm, counter=None,
                  attachment_pages=None, previous=None, package_amounts=None) -> tuple[dict, list[Failure]]:
    known = {package.package_no: package for package in understanding.packages}
    audit = {"protocol_version": "package-binding-v4", "bindings": [], "amount_bindings": [],
             "observation_bindings": [],
             "unresolved": [], "previous": previous or {}}
    unresolved_html_amount = any(amount is not None and amount.amount_yuan is not None and amount.amount_kind == "unknown"
                                 for amount in [understanding.summary_amount] + [p.package_amount for p in understanding.packages])
    # Original-number anchors with no unique directory match must reach the existing model
    # authority. In particular, a same-number lot and contract package cannot be chosen here.
    anchor_rows = [{**anchor, "source_ref": f"html:anchor:{index}"}
                   for index, anchor in enumerate(notice.package_anchors)
                   if anchor.get("amount_yuan") is not None
                   and hint_reference(anchor.get("package_no"), understanding.packages) is None]
    unresolved_html_amount = unresolved_html_amount or bool(anchor_rows)
    if not candidates and not package_amounts and not unresolved_html_amount:
        return audit, []
    # Sole membership is a container fact, not a content inference.
    if len(known) == 1 and not package_amounts and not unresolved_html_amount:
        number = next(iter(known))
        for candidate in candidates:
            if (candidate.package_no in (None, "", number)
                    or hint_reference(candidate.package_no, understanding.packages) == number):
                candidate.binding = {"suggested_package_no": candidate.binding.get("suggested_package_no", candidate.package_no),
                                     "package_no": number, "status": "bound", "method": "single_package"}
                candidate.package_no = number
                candidate.project_id = known[number].project_id
                audit["bindings"].append({"candidate_id": candidate.candidate_id, **candidate.binding})
            else:
                audit["unresolved"].append({"candidate_id": candidate.candidate_id,
                                             "reason": "package_not_in_registry"})
        return audit, []

    source_rows = [row | {"headers": table.headers, "section": table.section,
                          "before_text": table.before_text}
                   for table in notice.tables for row in html_rows(table)]
    referenced = {ref for candidate in candidates if candidate.evidence
                  for ref in candidate.evidence.source_refs}
    referenced.update(observation.source_ref for observation in (package_amounts or []))
    source_rows.extend(segment for page in (attachment_pages or [])
                       if any(segment["source_ref"] in referenced for segment in page.get("source_segments", []))
                       for segment in page.get("source_segments", []))
    source_rows.extend({"source_ref": f"html:body:{index}", **section}
                       for index, section in enumerate(notice.sections))
    source_rows.extend(notice.source_contexts.values())
    source_rows.extend(anchor_rows)
    source_rows = list({row["source_ref"]: row for row in source_rows}.values())
    references = {row["source_ref"] for row in source_rows}
    amounts = []
    for package in understanding.packages:
        amount = package.package_amount
        if amount is not None:
            amounts.append({"amount_ref": f"amount:package:{package.package_no}",
                            "raw_text": amount.raw_text, "amount_yuan": amount.amount_yuan,
                            "suggested_package_no": package.package_no, "amount_kind": amount.amount_kind,
                            "scope": amount.scope, "source_type": amount.source_type, "source_ref": amount.source_ref,
                            "source_refs": amount.source_refs})
        for index, alternative in enumerate(package.amount_alternatives):
            amounts.append({**alternative.model_dump(), "amount_ref": f"amount:package:{package.package_no}:alternative:{index}",
                            "suggested_package_no": package.package_no})
    if understanding.summary_amount:
        amounts.append({"amount_ref": "amount:summary", **understanding.summary_amount.model_dump()})
    # On later binding passes, a previously adopted anchor is already a package observation.
    represented_sources = {amount.get("source_ref") for amount in amounts if amount.get("source_ref")}
    anchor_amounts = [{"amount_ref": f"amount:{row['source_ref']}",
                       "raw_text": row.get("amount_raw"), "amount_yuan": row["amount_yuan"],
                       "suggested_package_no": row.get("package_no"), "scope": "package",
                       "amount_kind": "unknown", "source_type": "html",
                       "source_ref": row["source_ref"], "source_refs": [row["source_ref"]]}
                      for row in anchor_rows if row["source_ref"] not in represented_sources]
    amounts.extend(anchor_amounts)
    audit["html_anchor_amounts"] = anchor_amounts
    for candidate in candidates:
        if candidate.entity_type != "cob":
            continue
        obs = candidate.fields["total_price"]
        if obs.raw_value in (None, ""):
            continue
        header = (candidate.evidence.column_units if candidate.evidence else {}).get("total_price")
        parsed = parse_price_cell(obs.raw_value, header)
        amounts.append({"amount_ref": f"amount:{candidate.candidate_id}:total_price",
                        "raw_text": str(obs.raw_value), "amount_yuan": parsed["value"],
                        "candidate_id": candidate.candidate_id,
                        "source_class": candidate.source_class, "scope": "object", "amount_kind": "unknown",
                        "source_type": candidate.source.source_type,
                        "source_refs": candidate.evidence.source_refs if candidate.evidence else []})
    attachment_map = {}
    for index, observation in enumerate(package_amounts or []):
        ref = f"amount:attachment:{index}"
        attachment_map[ref] = observation
        amounts.append({**observation.model_dump(), "amount_ref": ref,
                        "suggested_package_no": observation.package_no,
                        "source_class": observation.file_class})
    amount_map = {amount["amount_ref"]: amount for amount in amounts}
    payload = {
        "known_packages": [package.model_dump() for package in understanding.packages],
        "project_name": understanding.project_name,
        "candidates": [{"candidate_id": item.candidate_id, "entity_type": item.entity_type,
                        "suggested_package_no": item.package_no,
                        "fields": {key: obs.normalized_value if obs.normalized_value is not None else obs.raw_value
                                   for key, obs in item.fields.items()},
                        "evidence": None if item.evidence is None else item.evidence.model_dump(
                            exclude={"row_text"} if item.evidence.source_refs and
                            all(ref in references for ref in item.evidence.source_refs) else set()),
                        "unmapped_fields": item.unmapped_fields,
                        "previous_binding": item.binding} for item in candidates],
        "source_rows": source_rows,
        "table_contexts": [{"table_index": table.table_index, "table_role": table.table_role,
                            "row_grain": table.row_grain, "package_scope": table.package_scope}
                           for table in tables.tables],
        "amount_observations": amounts,
    }
    ids = {candidate.candidate_id for candidate in candidates}

    def validate_body(parsed):
        bindings = parsed.get("bindings")
        if not isinstance(bindings, list) or any(not isinstance(item, dict) or
                not isinstance(item.get("candidate_id"), str) for item in bindings):
            return "需要 bindings 数组"
        if len(bindings) != len(ids) or {item.get("candidate_id") for item in bindings} != ids:
            return "bindings 必须逐一覆盖全部候选，不得遗漏或重复"
        for item in bindings:
            if item.get("package_no") is not None and (not isinstance(item["package_no"], str) or item["package_no"] not in known):
                return "包号不在输入包集合中，无法判定时填 null"
            refs = item.get("source_refs", [])
            if not isinstance(refs, list) or any(not isinstance(ref, str) or ref not in references for ref in refs):
                return "source_refs 必须引用提供的证据"
            if not isinstance(item.get("reason"), str) or not item["reason"].strip():
                return "每项归属决定需要 reason"
        assignments = parsed.get("amount_bindings")
        if (not isinstance(assignments, list) or len(assignments) != len(known)
                or any(not isinstance(item, dict) or not isinstance(item.get("package_no"), str)
                       for item in assignments)):
            return "amount_bindings 必须逐一覆盖全部包，可用 amount_ref=null 表示未决"
        if {item.get("package_no") for item in assignments if isinstance(item, dict)} != set(known):
            return "amount_bindings 包号不可重复或遗漏"
        chosen = []
        for item in assignments:
            if not isinstance(item.get("reason"), str) or not item["reason"].strip():
                return "每项金额归属决定需要 reason"
            ref = item.get("amount_ref")
            if ref is not None:
                if not isinstance(ref, str) or ref not in amount_map or item.get("amount_kind") != "award":
                    return "包中标金额只允许引用已给出的 award 金额，预算不能供价"
                if amount_map[ref].get("source_class") == "tender_requirement":
                    return "招标需求中的金额不能作为中标金额"
                if amount_map[ref].get("amount_kind") in {"budget", "ceiling"}:
                    return "已明确为预算或限价的观测不能作为中标金额"
                chosen.append(ref)
        if len(chosen) != len(set(chosen)):
            return "同一金额观测不可分配给多个包"
        observations = parsed.get("observation_bindings", [])
        if (not isinstance(observations, list) or any(not isinstance(item, dict) or
                not isinstance(item.get("amount_ref"), str) for item in observations)
                or len(observations) != len(attachment_map)
                or {item.get("amount_ref") for item in observations} != set(attachment_map)):
            return "observation_bindings 必须逐一覆盖附件金额观测"
        for item in observations:
            if item.get("package_no") is not None and (not isinstance(item["package_no"], str) or item["package_no"] not in known):
                return "附件金额归属包号不在公告包集合"
            if (item.get("amount_kind") not in ("award", "budget", "ceiling", "unknown")
                    or not isinstance(item.get("reason"), str) or not item["reason"].strip()):
                return "附件金额归属需要性质与理由"
            original_kind = attachment_map[item["amount_ref"]].amount_kind
            if original_kind in {"budget", "ceiling"} and item["amount_kind"] == "award":
                return "已声明预算或限价不能经归属分配变为成交价"
        by_observation = {item["amount_ref"]: item for item in observations}
        for item in assignments:
            observed = by_observation.get(item.get("amount_ref"))
            if observed and (observed["package_no"] != item["package_no"] or observed["amount_kind"] != "award"):
                return "包供值必须与附件观测归属及成交性质一致"
        return ""

    try:
        parsed = complete_json(llm, step="bind_packages", prompt_version="package-binding-v5",
                               user=json.dumps(payload, ensure_ascii=False), validate=validate_body, counter=counter)
    except (LLMError, ValueError) as exc:
        audit["fallback"] = str(exc)
        audit["unresolved"] = [{"candidate_id": item.candidate_id, "reason": "binding_model_failed"}
                                for item in candidates]
        for ref, observation in attachment_map.items():
            observation.binding = {"amount_ref": ref, "status": "unresolved", "reason": "binding_model_failed"}
            audit["unresolved"].append(observation.binding)
        code = ("llm_call_failed" if isinstance(exc, LLMError) else "llm_validator_incompatible"
                if isinstance(exc, LLMValidationError) and any(issue.category == "implementation" for issue in exc.issues)
                else "llm_schema_invalid")
        return audit, [Failure(failure_code=code, failure_message=str(exc),
                               location="bind_packages")]
    by_id = {candidate.candidate_id: candidate for candidate in candidates}
    for binding in parsed["bindings"]:
        candidate = by_id[binding["candidate_id"]]
        number = binding["package_no"]
        candidate.binding = {"suggested_package_no": candidate.binding.get("suggested_package_no", candidate.package_no),
                             **binding, "status": "bound" if number else "unresolved", "method": "model"}
        candidate.package_no = number
        candidate.project_id = None if number is None else make_project_id(understanding.project_name, number)
        audit["bindings"].append(candidate.binding)
        if number is None:
            audit["unresolved"].append(binding)
    for binding in parsed["amount_bindings"]:
        package = known[binding["package_no"]]
        source = amount_map.get(binding.get("amount_ref"))
        package.package_amount = None if source is None else Amount(
            raw_text=source["raw_text"], amount_yuan=source.get("amount_yuan"), scope="package",
            amount_kind="award", source_type=source.get("source_type", "html"), source_ref=source.get("source_ref"),
            source_refs=source.get("source_refs", []) or ([source["source_ref"]] if source.get("source_ref") else []),
            binding=binding)
        audit["amount_bindings"].append(binding)
    for binding in parsed.get("observation_bindings", []):
        observation = attachment_map[binding["amount_ref"]]
        observation.binding = {"suggested_package_no": observation.binding.get("suggested_package_no", observation.package_no),
                               **binding, "status": "bound" if binding["package_no"] else "unresolved"}
        if binding["package_no"]:
            observation.package_no = binding["package_no"]
        observation.amount_kind = binding["amount_kind"]
        audit["observation_bindings"].append(observation.binding)
        if binding["package_no"] is None:
            audit["unresolved"].append(observation.binding)
    return audit, []
