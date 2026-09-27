"""Step 8. Merge candidates inside one project.

HTML award detail owns the object list when it is a real line list.
Attachments then only fill empty fields on a matching name.
Tender files never add objects. Empty values do not split two rows.
"""

from __future__ import annotations

import re

from ict.config import COB_FIELDS, SUB_FIELDS
from ict.schemas import AnnouncementUnderstanding, Candidate, Cob, Failure, MergedProjects, Project, Sub

PRICE_FIELDS = ("brand", "unit_price", "quantity", "unit", "total_price")


def _value(candidate: Candidate, key: str):
    field = candidate.fields[key]
    if field.normalized_value is not None:
        return field.normalized_value
    if field.status == "present":
        return field.raw_value
    return None


def _norm_name(value) -> str:
    text = "" if value is None else str(value)
    return re.sub(r"\s+", "", text).replace("（", "(").replace("）", ")")


def _number(value):
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", "").replace("，", ""))
    except ValueError:
        return None


def _same(left, right) -> bool:
    if left == right:
        return True
    left_number, right_number = _number(left), _number(right)
    if left_number is None or right_number is None:
        return False
    return abs(left_number - right_number) <= 0.01


def _html_detail(candidate: Candidate) -> bool:
    return (
        candidate.source.source_type == "html"
        and candidate.source_priority >= 70
        and "summary_row" not in candidate.issues
        and candidate.fields["object_name"].status != "points_to_attachment"
    )


def _tender(candidate: Candidate) -> bool:
    return candidate.source_class == "tender_requirement"


def _contradicts(left: Candidate, right: Candidate) -> bool:
    if _html_detail(left) != _html_detail(right):
        return False
    for key in ("quantity", "unit_price"):
        left_value, right_value = _value(left, key), _value(right, key)
        if left_value not in (None, "") and right_value not in (None, "") and not _same(left_value, right_value):
            return True
    return False


def _completeness(candidate: Candidate) -> int:
    keys = COB_FIELDS if candidate.entity_type == "cob" else SUB_FIELDS
    return sum(1 for key in keys if _value(candidate, key) not in (None, ""))


def _copy_field(base: Candidate, key: str, value) -> None:
    base.fields[key].normalized_value = value
    base.fields[key].status = "present"


def _reconcile(group: list[Candidate], conflicts: list[dict]) -> Candidate:
    base = max(group, key=lambda item: (int(_html_detail(item)), item.source_priority, _completeness(item)))
    for extra in group:
        if extra is base:
            continue
        extra_bad = "total_price_mismatch" in extra.warnings
        base_bad = "total_price_mismatch" in base.warnings
        for key in COB_FIELDS:
            left, right = _value(base, key), _value(extra, key)
            if right in (None, ""):
                continue
            if left in (None, ""):
                if extra_bad and key in PRICE_FIELDS and not base_bad:
                    continue
                _copy_field(base, key, right)
                continue
            if _same(left, right):
                continue
            if key in PRICE_FIELDS and _html_detail(base) and not base_bad:
                continue
            if key in PRICE_FIELDS and extra_bad and not base_bad:
                continue
            if key in PRICE_FIELDS and base_bad and not extra_bad:
                _copy_field(base, key, right)
                base.warnings = [item for item in base.warnings if item != "total_price_mismatch"]
                continue
            base.fields[key].status = "conflict"
            conflicts.append(
                {"field": key, "candidate_ids": [base.candidate_id, extra.candidate_id], "values": [left, right]}
            )
    return base


def _clusters(items: list[Candidate]) -> list[list[Candidate]]:
    by_name: dict[str, list[Candidate]] = {}
    for candidate in items:
        name = _norm_name(_value(candidate, "object_name"))
        if not name:
            continue
        by_name.setdefault(name, []).append(candidate)
    groups: list[list[Candidate]] = []
    for members in by_name.values():
        buckets: list[list[Candidate]] = []
        for candidate in members:
            placed = False
            for bucket in buckets:
                if not any(_contradicts(candidate, other) for other in bucket):
                    bucket.append(candidate)
                    placed = True
                    break
            if not placed:
                buckets.append([candidate])
        groups.extend(buckets)
    return groups


def _line_item(candidate: Candidate) -> bool:
    return _value(candidate, "quantity") not in (None, "") or _value(candidate, "unit_price") not in (None, "")


def _select_cobs(items: list[Candidate], unmatched: list[dict]) -> list[Candidate]:
    html = [item for item in items if item.entity_type == "cob" and item.source.source_type == "html"]
    attached = [item for item in items if item.entity_type == "cob" and item.source.source_type != "html"]
    named = [item for item in html if _html_detail(item) and _value(item, "object_name")]
    priced = [item for item in named if _line_item(item)]
    names = {_norm_name(_value(item, "object_name")) for item in (priced or named)}
    single_bare_name = not priced and len(names) == 1
    pool: list[Candidate] = []
    if names and not single_bare_name:
        pool.extend(priced or named)
        for candidate in attached:
            if _tender(candidate):
                unmatched.append({"candidate_id": candidate.candidate_id, "reason": "招标需求不能新增标的"})
                continue
            if _norm_name(_value(candidate, "object_name")) in names:
                pool.append(candidate)
            else:
                unmatched.append({"candidate_id": candidate.candidate_id, "reason": "HTML 明细清单已封闭，附件名称未匹配"})
        for candidate in html:
            if candidate not in pool:
                unmatched.append({"candidate_id": candidate.candidate_id, "reason": "HTML 汇总行不进入封闭清单"})
        return pool
    for candidate in attached:
        if _tender(candidate):
            unmatched.append({"candidate_id": candidate.candidate_id, "reason": "招标需求不能新增标的"})
            continue
        pool.append(candidate)
    if single_bare_name and any(_line_item(item) for item in pool):
        for candidate in named:
            unmatched.append({"candidate_id": candidate.candidate_id, "reason": "HTML 汇总行已由附件明细替换"})
        return pool
    pool.extend(named)
    if any("summary_row" not in item.issues and _value(item, "object_name") for item in pool):
        kept = []
        for candidate in pool:
            if "summary_row" in candidate.issues:
                unmatched.append({"candidate_id": candidate.candidate_id, "reason": "HTML 汇总行已由明细替换"})
                continue
            kept.append(candidate)
        pool = kept
    return pool


def merge_projects(run_id: str, understanding: AnnouncementUnderstanding, candidates: list[Candidate]) -> MergedProjects:
    known = {package.package_no: package for package in understanding.packages}
    conflicts: list[dict] = []
    unassigned: list[dict] = []
    unmatched: list[dict] = []
    grouped: dict[str, list[Candidate]] = {package.project_id: [] for package in understanding.packages}
    for candidate in candidates:
        if candidate.project_id not in grouped:
            unassigned.append({"candidate_id": candidate.candidate_id, "reason": "无法确定包号"})
            continue
        if candidate.package_no and candidate.package_no not in known:
            candidate.validation_errors.append("package_not_found")
            unassigned.append({"candidate_id": candidate.candidate_id, "reason": "包号不在公告包列表"})
            continue
        grouped[candidate.project_id].append(candidate)
    projects: list[Project] = []
    single = understanding.package_mode == "single"
    for package in understanding.packages:
        items = grouped.get(package.project_id, [])
        cobs, cob_ids, cob_conflicts, cob_unmatched = _merge_cobs(items)
        subs, sub_ids, sub_conflicts, _ = _merge_entity(items, "sub")
        conflicts.extend(cob_conflicts + sub_conflicts)
        unmatched.extend(cob_unmatched)
        suppliers = []
        product_names = []
        for cob in cobs:
            if cob.product_supplier:
                product_names.append(cob.product_supplier)
        unique_products = list(dict.fromkeys(product_names))
        for sub in subs:
            suppliers.append(
                Sub(
                    supplier_name=sub.supplier_name,
                    score=sub.score,
                    is_winner=bool(sub.is_winner),
                    cooperative_product_suppliers=unique_products if sub.is_winner else [],
                )
            )
        amount = None
        if package.package_amount and package.package_amount.amount_yuan is not None:
            amount = package.package_amount.amount_yuan
        elif single and understanding.summary_amount and understanding.summary_amount.amount_yuan is not None:
            amount = understanding.summary_amount.amount_yuan
        projects.append(
            Project(
                project_id=package.project_id,
                source_project_no=understanding.source_project_no,
                project_name=understanding.project_name or "",
                package_no=package.package_no,
                purchaser=understanding.purchaser,
                package_total_amount=amount,
                cobs=cobs,
                subs=suppliers,
                provenance={"cob_candidate_ids": cob_ids, "sub_candidate_ids": sub_ids},
            )
        )
    status = "partial" if conflicts or unassigned or unmatched else "success"
    return MergedProjects(
        run_id=run_id,
        status=status,
        projects=projects,
        conflicts=conflicts,
        unmatched_summary_rows=unmatched,
        unassigned_candidates=unassigned,
        failures=[],
    )


def _merge_cobs(items: list[Candidate]):
    conflicts: list[dict] = []
    unmatched: list[dict] = []
    chosen = []
    ids = []
    for group in _clusters(_select_cobs(items, unmatched)):
        candidate = _reconcile(group, conflicts)
        if not _value(candidate, "object_name"):
            continue
        ids.append(candidate.candidate_id)
        chosen.append(
            Cob(
                object_name=str(_value(candidate, "object_name")),
                category_code=_value(candidate, "category_code"),
                category_name=_value(candidate, "category_name"),
                category_type=_value(candidate, "category_type"),
                brand=_value(candidate, "brand"),
                product_supplier=_value(candidate, "product_supplier"),
                spec_model=_value(candidate, "spec_model"),
                unit_price=_value(candidate, "unit_price"),
                quantity=_value(candidate, "quantity"),
                unit=_value(candidate, "unit"),
                total_price=_value(candidate, "total_price"),
            )
        )
    return chosen, ids, conflicts, unmatched


def _merge_entity(items: list[Candidate], entity_type: str):
    chosen: dict[tuple, Candidate] = {}
    order: list[tuple] = []
    conflicts: list[dict] = []
    unmatched: list[dict] = []
    keys = SUB_FIELDS
    for candidate in items:
        if candidate.entity_type != entity_type:
            continue
        key = (_norm_name(_value(candidate, "supplier_name")),)
        if not key[0]:
            continue
        if key not in chosen:
            chosen[key] = candidate
            order.append(key)
            continue
        current = chosen[key]
        replace = candidate.source_priority > current.source_priority or (
            candidate.source_priority == current.source_priority and _completeness(candidate) > _completeness(current)
        )
        base, extra = (candidate, current) if replace else (current, candidate)
        for field in keys:
            left = _value(base, field)
            right = _value(extra, field)
            if left in (None, "") and right not in (None, ""):
                base.fields[field].normalized_value = right
                base.fields[field].status = "present"
            elif field == "is_winner" and left in (None, False, "") and right in (True, "是", "true"):
                base.fields[field].normalized_value = right
                base.fields[field].status = "present"
            elif left not in (None, "") and right not in (None, "") and not _same(left, right) and field != "is_winner":
                base.fields[field].status = "conflict"
                conflicts.append(
                    {"field": field, "candidate_ids": [base.candidate_id, extra.candidate_id], "values": [left, right]}
                )
        chosen[key] = base
    merged = []
    ids = []
    for key in order:
        candidate = chosen[key]
        ids.append(candidate.candidate_id)
        if not _value(candidate, "supplier_name"):
            continue
        winner = _value(candidate, "is_winner")
        merged.append(
            Sub(
                supplier_name=str(_value(candidate, "supplier_name")),
                score=_value(candidate, "score"),
                is_winner=bool(winner) and str(winner).lower() not in {"false", "否", "0"},
            )
        )
    return merged, ids, conflicts, unmatched


def consistency_checks(merged: MergedProjects) -> list[Failure]:
    checks: list[dict] = []
    for project in merged.projects:
        if not project.cobs:
            checks.append({"package_no": project.package_no, "check": "missing_cob"})
        if not any(sub.is_winner for sub in project.subs):
            checks.append({"package_no": project.package_no, "check": "missing_winner"})
        totals = [cob.total_price for cob in project.cobs if isinstance(cob.total_price, (int, float))]
        amount = project.package_total_amount
        if amount and totals:
            summed = sum(totals)
            if abs(summed - amount) / abs(amount) > 0.08:
                checks.append(
                    {
                        "package_no": project.package_no,
                        "check": "amount_mismatch",
                        "line_sum": summed,
                        "package_total": amount,
                    }
                )
    merged.checks = checks
    if not checks:
        return []
    if merged.status == "success":
        merged.status = "partial"
    return [
        Failure(
            failure_code="consistency_check_failed",
            failure_message="；".join(f"{item['package_no']}:{item['check']}" for item in checks)[:500],
        )
    ]
