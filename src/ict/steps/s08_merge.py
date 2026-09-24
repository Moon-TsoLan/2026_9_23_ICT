"""Step 8. Merge candidates inside one project. Lower priority only fills empty fields."""

from __future__ import annotations

from ict.config import COB_FIELDS, SUB_FIELDS
from ict.schemas import AnnouncementUnderstanding, Candidate, Cob, MergedProjects, Project, Sub

COB_KEY = ("object_name", "brand", "spec_model", "quantity", "unit")


def _value(candidate: Candidate, key: str):
    field = candidate.fields[key]
    if field.normalized_value is not None:
        return field.normalized_value
    if field.status == "present":
        return field.raw_value
    return None


def _business_key(candidate: Candidate) -> tuple:
    keys = COB_KEY if candidate.entity_type == "cob" else ("supplier_name",)
    return tuple(_value(candidate, key) for key in keys)


def _completeness(candidate: Candidate) -> int:
    keys = COB_FIELDS if candidate.entity_type == "cob" else SUB_FIELDS
    return sum(1 for key in keys if _value(candidate, key) not in (None, ""))


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
        cobs, cob_ids, cob_conflicts, cob_unmatched = _merge_entity(items, "cob")
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


def _merge_entity(items: list[Candidate], entity_type: str):
    chosen: dict[tuple, Candidate] = {}
    order: list[tuple] = []
    conflicts: list[dict] = []
    unmatched: list[dict] = []
    keys = COB_FIELDS if entity_type == "cob" else SUB_FIELDS
    for candidate in items:
        if candidate.entity_type != entity_type:
            continue
        if entity_type == "cob" and "summary_row" in candidate.issues:
            unmatched.append({"candidate_id": candidate.candidate_id, "reason": "HTML 汇总行未在附件明细中找到对应 COB"})
            continue
        key = _business_key(candidate)
        if key not in chosen:
            chosen[key] = candidate
            order.append(key)
            continue
        current = chosen[key]
        replace = False
        if candidate.source_priority > current.source_priority:
            replace = True
        elif candidate.source_priority == current.source_priority and _completeness(candidate) > _completeness(current):
            replace = True
        base, extra = (candidate, current) if replace else (current, candidate)
        for field in keys:
            left = _value(base, field)
            right = _value(extra, field)
            if left in (None, "") and right not in (None, ""):
                base.fields[field].normalized_value = right
                base.fields[field].status = "present"
            elif left not in (None, "") and right not in (None, "") and left != right:
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
        if entity_type == "cob":
            if not _value(candidate, "object_name"):
                continue
            merged.append(
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
        else:
            if not _value(candidate, "supplier_name"):
                continue
            merged.append(
                Sub(
                    supplier_name=str(_value(candidate, "supplier_name")),
                    score=_value(candidate, "score"),
                    is_winner=bool(_value(candidate, "is_winner")),
                )
            )
    return merged, ids, conflicts, unmatched
