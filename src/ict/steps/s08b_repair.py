"""Step 8b. Repair packages that fail a hard check, then check again.

Deterministic repairs run first. The model may only choose among price bundles
already recorded in step 8; it never writes a new value.
"""

from __future__ import annotations

import json
from collections import Counter

from ict.llm import LLMClient, LLMError, complete_json
from ict.schemas import Failure, MergedProjects, Project, Sub
from ict.steps.s08_merge import _norm_name, amount_checked, consistency_checks, package_checks

PRICE_KEYS = ("unit_price", "quantity", "unit", "total_price")


def _score(project: Project, amount_ok: bool = True) -> tuple[int, float]:
    checks = package_checks(project, amount_checked=amount_ok)
    hard = sum(1 for item in checks if item["level"] in {"hard", "medium"})
    totals = [cob.total_price for cob in project.cobs if isinstance(cob.total_price, (int, float))]
    amount = project.package_total_amount
    gap = abs(sum(totals) - amount) / abs(amount) if amount and totals else 0.0
    return hard, gap


def _mark_winners(project: Project, names: list[str], repairs: list[dict]) -> None:
    wanted = {_norm_name(name) for name in names}
    for sub in project.subs:
        if _norm_name(sub.supplier_name) in wanted and not sub.is_winner:
            sub.is_winner = True
            repairs.append({"package_no": project.package_no, "action": "winner_from_award_text", "supplier_name": sub.supplier_name})
    if not any(sub.is_winner for sub in project.subs):
        known = {_norm_name(sub.supplier_name) for sub in project.subs}
        for name in dict.fromkeys(names):
            if _norm_name(name) in known:
                continue
            project.subs.append(Sub(supplier_name=name, score=None, is_winner=True))
            repairs.append({"package_no": project.package_no, "action": "winner_added_from_award_text", "supplier_name": name})
    products = list(dict.fromkeys(cob.product_supplier for cob in project.cobs if cob.product_supplier))
    for sub in project.subs:
        sub.cooperative_product_suppliers = products if sub.is_winner else []


def _apply(project: Project, alternatives: list[dict], choices: list[dict]) -> Project:
    trial = project.model_copy(deep=True)
    by_cluster = {item["cluster_id"]: item for item in alternatives}
    for choice in choices:
        cluster = by_cluster.get(choice.get("cluster_id"))
        if cluster is None:
            continue
        option = next((item for item in cluster["options"] if item["candidate_id"] == choice.get("candidate_id")), None)
        index = cluster["cob_index"]
        if option is None or index >= len(trial.cobs):
            continue
        cob = trial.cobs[index]
        keep_unit = option.get("unit") in (None, "") and option.get("quantity") == cob.quantity
        for key in PRICE_KEYS:
            if key == "unit" and keep_unit:
                continue
            value = option.get(key)
            setattr(cob, key, None if value in (None, "") else (str(value) if key == "unit" else float(value)))
        if cob.total_price is None and cob.unit_price is not None and cob.quantity is not None:
            cob.total_price = round(cob.unit_price * cob.quantity, 4)
    return trial


def _ask_model(project: Project, alternatives: list[dict], violations: list[dict], llm: LLMClient, counter: Counter | None) -> list[dict]:
    payload = {
        "package_no": project.package_no,
        "package_total_amount": project.package_total_amount,
        "violations": violations,
        "clusters": [
            {
                "cluster_id": item["cluster_id"],
                "object_name": item["object_name"],
                "current_candidate_id": item["chosen"],
                "options": item["options"],
            }
            for item in alternatives
        ],
    }
    allowed = {item["cluster_id"]: {option["candidate_id"] for option in item["options"]} for item in alternatives}

    def validate(parsed: dict) -> str:
        choices = parsed.get("choices")
        if not isinstance(choices, list):
            return "需要 choices 数组"
        for choice in choices:
            if choice.get("cluster_id") not in allowed:
                return f"cluster_id {choice.get('cluster_id')} 不在输入里"
            if choice.get("candidate_id") not in allowed[choice["cluster_id"]]:
                return f"candidate_id {choice.get('candidate_id')} 不是该簇的备选"
        return ""

    parsed = complete_json(
        llm,
        step="repair_packages",
        prompt_version="repair-package-v1",
        user=json.dumps(payload, ensure_ascii=False),
        validate=validate,
        counter=counter,
    )
    return parsed.get("choices") or []


def repair_packages(
    merged: MergedProjects,
    winner_hints: dict[str, list[str]],
    llm: LLMClient | None,
    counter: Counter | None = None,
) -> list[Failure]:
    failures: list[Failure] = []
    for position, project in enumerate(merged.projects):
        amount_ok = amount_checked(merged, project)
        checks = [item for item in package_checks(project, amount_checked=amount_ok)
                  if item["level"] in {"hard", "medium"}]
        if not checks:
            continue
        if any(item["check"] == "missing_winner" for item in checks):
            _mark_winners(project, winner_hints.get(project.package_no, []), merged.repairs)
        # Only an overflow can be repaired by picking another reading. A shortfall is not a defect
        # (the object list may simply be partial) and a row with no price has no reading to pick,
        # so both go to review instead of to the model.
        price_checks = [item for item in checks if item["check"] == "amount_overflow"]
        alternatives = merged.alternatives.get(project.project_id) or []
        if not price_checks or not alternatives or llm is None:
            continue
        try:
            choices = _ask_model(project, alternatives, price_checks, llm, counter)
        except (LLMError, ValueError) as exc:
            code = "llm_call_failed" if isinstance(exc, LLMError) else "llm_schema_invalid"
            failures.append(Failure(failure_code=code, failure_message=str(exc), location=project.package_no))
            continue
        if not choices:
            merged.repairs.append({"package_no": project.package_no, "action": "model_no_choice"})
            continue
        trial = _apply(project, alternatives, choices)
        if _score(trial, amount_ok) < _score(project, amount_ok):
            merged.projects[position] = trial
            merged.repairs.append({"package_no": project.package_no, "action": "price_bundle_by_model", "choices": choices})
        else:
            merged.repairs.append({"package_no": project.package_no, "action": "model_choice_rejected", "choices": choices})
    failures.extend(consistency_checks(merged))
    return failures
