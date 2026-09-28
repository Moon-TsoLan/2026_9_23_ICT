"""Step 8. Merge candidates inside one project.

HTML award detail owns the object list when it is a real line list.
Attachments then only join a matching name. Tender files never add objects.
Price fields move as one bundle; the package amount decides between bundles.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ict.config import COB_FIELDS, SUB_FIELDS
from ict.schemas import AnnouncementUnderstanding, Candidate, Cob, Failure, MergedProjects, Project, Sub

PRICE_BUNDLE = ("unit_price", "quantity", "unit", "total_price")
LINE_FIELDS = ("brand", "spec_model", "quantity", "unit_price", "total_price")
ROW_TOLERANCE = 0.05
AMOUNT_TOLERANCE = 0.08
MEMBER_RE = re.compile(r"\([^()]*成员[^()]*\)")


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
    if value in (None, "") or isinstance(value, bool):
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


def _authority(candidate: Candidate) -> tuple:
    return (int(_html_detail(candidate)), candidate.source_priority, _completeness(candidate))


def _completeness(candidate: Candidate) -> int:
    keys = COB_FIELDS if candidate.entity_type == "cob" else SUB_FIELDS
    return sum(1 for key in keys if _value(candidate, key) not in (None, ""))


def _derived_total(candidate: Candidate) -> bool:
    normalization = candidate.fields["total_price"].normalization or {}
    return bool(normalization.get("filled_from"))


def _has_price(candidate: Candidate) -> bool:
    return any(_value(candidate, key) not in (None, "") for key in ("unit_price", "quantity", "total_price"))


def _row_bad(values: dict) -> bool:
    price, quantity, total = _number(values.get("unit_price")), _number(values.get("quantity")), _number(values.get("total_price"))
    if price is None or quantity is None or not total:
        return False
    return abs(price * quantity - total) / abs(total) > ROW_TOLERANCE


def _bundle_of(candidate: Candidate) -> dict:
    return {key: _value(candidate, key) for key in PRICE_BUNDLE}


def _compatible(left: dict, right: dict) -> bool:
    for key in PRICE_BUNDLE:
        a, b = left.get(key), right.get(key)
        if a in (None, "") or b in (None, ""):
            continue
        if not _same(a, b):
            return False
    return True


def _points_line(candidate: Candidate) -> bool:
    if "line_fields_point_to_attachment" in candidate.issues:
        return True
    return any(candidate.fields[key].status == "points_to_attachment" for key in LINE_FIELDS)


def _line_item(candidate: Candidate) -> bool:
    return _value(candidate, "quantity") not in (None, "") or _value(candidate, "unit_price") not in (None, "")


def is_project_name(name, project_name) -> bool:
    item, project = _norm_name(name), _norm_name(project_name)
    if not item or not project:
        return False
    if item == project or project in item:
        return True
    return item in project and len(item) >= 0.8 * len(project)


@dataclass
class _Cluster:
    base: Candidate
    options: list[Candidate]
    chosen: Candidate | None
    bundle: dict = field(default_factory=dict)
    derived: bool = False


def _copy_field(base: Candidate, key: str, value) -> None:
    base.fields[key].normalized_value = value
    base.fields[key].status = "present"


def _fill_bundle(chosen: Candidate, options: list[Candidate]) -> tuple[dict, bool]:
    bundle = _bundle_of(chosen)
    derived = _derived_total(chosen)
    for other in sorted(options, key=_authority, reverse=True):
        if other is chosen:
            continue
        values = _bundle_of(other)
        if _row_bad(values) or not _compatible(bundle, values):
            continue
        for key in PRICE_BUNDLE:
            if bundle[key] in (None, "") and values[key] not in (None, ""):
                bundle[key] = values[key]
                if key == "total_price":
                    derived = _derived_total(other)
    return bundle, derived


def _reconcile(group: list[Candidate], conflicts: list[dict], list_owner: str) -> _Cluster:
    base = max(group, key=_authority)
    for extra in sorted(group, key=_authority, reverse=True):
        if extra is base:
            continue
        for key in COB_FIELDS:
            if key in PRICE_BUNDLE:
                continue
            if key == "product_supplier" and list_owner == "html" and extra.source.source_type != "html":
                continue
            left, right = _value(base, key), _value(extra, key)
            if right in (None, ""):
                continue
            if left in (None, ""):
                _copy_field(base, key, right)
                continue
            if _same(left, right) or (key == "brand" and _html_detail(base)):
                continue
            base.fields[key].status = "conflict"
            conflicts.append({"field": key, "candidate_ids": [base.candidate_id, extra.candidate_id], "values": [left, right]})
    if list_owner == "html" and base.source.source_type != "html":
        base.fields["product_supplier"].normalized_value = None
        base.fields["product_supplier"].status = "missing"
    options = [item for item in group if _has_price(item)]
    if not options:
        return _Cluster(base=base, options=[], chosen=None, bundle={key: None for key in PRICE_BUNDLE})
    ranked = sorted(options, key=_authority, reverse=True)
    good = [item for item in ranked if not _row_bad(_bundle_of(item))]
    chosen = (good or ranked)[0]
    bundle, derived = _fill_bundle(chosen, options)
    for other in options:
        if other is chosen or _compatible(bundle, _bundle_of(other)):
            continue
        conflicts.append(
            {
                "field": "price_bundle",
                "candidate_ids": [chosen.candidate_id, other.candidate_id],
                "values": [_printable(bundle), _printable(_bundle_of(other))],
            }
        )
    return _Cluster(base=base, options=options, chosen=chosen, bundle=bundle, derived=derived)


def _printable(bundle: dict) -> dict:
    return {key: bundle.get(key) for key in PRICE_BUNDLE}


def _contradicts(left: Candidate, right: Candidate) -> bool:
    for key in ("quantity", "unit_price"):
        left_value, right_value = _value(left, key), _value(right, key)
        if left_value not in (None, "") and right_value not in (None, "") and not _same(left_value, right_value):
            return True
    return False


def _clusters(items: list[Candidate], closed: bool) -> list[list[Candidate]]:
    """Same name forms one row. In a closed list only HTML rows may start a row."""
    by_name: dict[str, list[Candidate]] = {}
    for candidate in items:
        name = _norm_name(_value(candidate, "object_name"))
        if name:
            by_name.setdefault(name, []).append(candidate)
    groups: list[list[Candidate]] = []
    for members in by_name.values():
        leaders = [item for item in members if _html_detail(item)] if closed else members
        followers = [item for item in members if not _html_detail(item)] if closed else []
        if not leaders:
            leaders, followers = members, []
        buckets: list[list[Candidate]] = []
        for candidate in leaders:
            for bucket in buckets:
                if not any(_contradicts(candidate, other) for other in bucket):
                    bucket.append(candidate)
                    break
            else:
                buckets.append([candidate])
        for candidate in followers:
            target = next(
                (bucket for bucket in buckets if _compatible(_bundle_of(bucket[0]), _bundle_of(candidate))),
                buckets[0],
            )
            target.append(candidate)
        groups.extend(buckets)
    return groups


def _select_cobs(items: list[Candidate], unmatched: list[dict], project_name: str | None) -> tuple[list[Candidate], str]:
    """Return the candidate pool and who owns the object list: html or attachment."""
    html = [item for item in items if item.entity_type == "cob" and item.source.source_type == "html"]
    attached = []
    for item in items:
        if item.entity_type != "cob" or item.source.source_type == "html":
            continue
        if _tender(item):
            unmatched.append({"candidate_id": item.candidate_id, "reason": "招标需求不能新增标的"})
            continue
        attached.append(item)
    named = [item for item in html if _html_detail(item) and _value(item, "object_name")]
    project_rows = [item for item in named if is_project_name(_value(item, "object_name"), project_name)]
    others = [item for item in named if item not in project_rows]
    if project_rows and others:
        named = others
        _move_project_price(project_rows, others, unmatched)
    priced = [item for item in named if _line_item(item)]
    names = {_norm_name(_value(item, "object_name")) for item in (priced or named)}
    open_single = not priced and len(names) == 1 and any(_points_line(item) for item in named)
    if names and not open_single:
        pool = list(priced or named)
        for candidate in attached:
            if _norm_name(_value(candidate, "object_name")) in names:
                pool.append(candidate)
            else:
                unmatched.append({"candidate_id": candidate.candidate_id, "reason": "HTML 明细清单已封闭，附件名称未匹配"})
        for candidate in html:
            if candidate not in pool and candidate not in project_rows:
                unmatched.append({"candidate_id": candidate.candidate_id, "reason": "HTML 汇总行不进入封闭清单"})
        return pool, "html"
    pool = list(attached)
    if open_single and any(_line_item(item) for item in pool):
        for candidate in named:
            unmatched.append({"candidate_id": candidate.candidate_id, "reason": "HTML 汇总行已由附件明细替换"})
        return pool, "attachment"
    pool.extend(named)
    if any("summary_row" not in item.issues and _value(item, "object_name") for item in pool):
        kept = []
        for candidate in pool:
            if "summary_row" in candidate.issues:
                unmatched.append({"candidate_id": candidate.candidate_id, "reason": "HTML 汇总行已由明细替换"})
                continue
            kept.append(candidate)
        pool = kept
    owner = "attachment" if any(item.source.source_type != "html" for item in pool) else "html"
    return pool, owner


def _move_project_price(project_rows: list[Candidate], others: list[Candidate], unmatched: list[dict]) -> None:
    """A row named after the whole project is not an object; its price goes to the only real object."""
    target_names = {_norm_name(_value(item, "object_name")) for item in others}
    single_target = len(target_names) == 1 and not any(_has_price(item) for item in others)
    for row in project_rows:
        moved = False
        if single_target and _has_price(row):
            for target in others:
                for key in PRICE_BUNDLE:
                    value = _value(row, key)
                    if value not in (None, "") and _value(target, key) in (None, ""):
                        _copy_field(target, key, value)
                        target.fields[key].normalization = {"filled_from": "project_row"} if key == "total_price" else None
            moved = True
        reason = "项目全称行，价格并入唯一标的" if moved else "项目全称行不作为标的"
        unmatched.append({"candidate_id": row.candidate_id, "reason": reason})


def _package_total(clusters: list[_Cluster]) -> float | None:
    totals = [_number(item.bundle.get("total_price")) for item in clusters]
    totals = [value for value in totals if value is not None]
    return sum(totals) if totals else None


def _deviation(total: float | None, amount: float | None) -> float | None:
    if total is None or not amount:
        return None
    return (total - amount) / abs(amount)


def _swap_by_amount(clusters: list[_Cluster], amount: float | None, list_owner: str) -> list[dict]:
    """Change the fewest bundles that bring the line total closer to the package amount."""
    swaps: list[dict] = []
    for _ in range(len(clusters) + 1):
        total = _package_total(clusters)
        deviation = _deviation(total, amount)
        if deviation is None:
            return swaps
        overflow = deviation > AMOUNT_TOLERANCE
        underflow = deviation < -AMOUNT_TOLERANCE and list_owner != "html"
        if not overflow and not underflow:
            return swaps
        best = None
        for cluster in clusters:
            current = _number(cluster.bundle.get("total_price")) or 0
            for option in cluster.options:
                if option is cluster.chosen or _row_bad(_bundle_of(option)):
                    continue
                bundle, derived = _fill_bundle(option, cluster.options)
                value = _number(bundle.get("total_price"))
                if value is None:
                    continue
                new_gap = abs(total - current + value - amount)
                if new_gap + 1e-6 < abs(total - amount) and (best is None or new_gap < best[0]):
                    best = (new_gap, cluster, option, bundle, derived)
        if best is None:
            return swaps
        _, cluster, option, bundle, derived = best
        swaps.append(
            {
                "action": "price_bundle_by_amount",
                "from": None if cluster.chosen is None else cluster.chosen.candidate_id,
                "to": option.candidate_id,
            }
        )
        cluster.chosen, cluster.bundle, cluster.derived = option, bundle, derived
    return swaps


def _alternatives(clusters: list[_Cluster]) -> list[dict]:
    found = []
    for index, cluster in enumerate(clusters):
        distinct: list[Candidate] = []
        seen: set[tuple] = set()
        for option in cluster.options:
            key = tuple(_number(_value(option, name)) for name in ("unit_price", "quantity", "total_price"))
            if key in seen:
                continue
            seen.add(key)
            distinct.append(option)
        if len(distinct) < 2:
            continue
        found.append(
            {
                "cluster_id": index,
                "cob_index": index,
                "object_name": str(_value(cluster.base, "object_name")),
                "chosen": None if cluster.chosen is None else cluster.chosen.candidate_id,
                "options": [
                    {
                        "candidate_id": option.candidate_id,
                        "source_type": option.source.source_type,
                        "file_id": option.source.file_id,
                        "file_class": option.source_class,
                        "source_priority": option.source_priority,
                        **_printable(_bundle_of(option)),
                        "total_is_derived": _derived_total(option),
                    }
                    for option in distinct
                ],
            }
        )
    return found


def merge_projects(run_id: str, understanding: AnnouncementUnderstanding, candidates: list[Candidate]) -> MergedProjects:
    known = {package.package_no: package for package in understanding.packages}
    conflicts: list[dict] = []
    unassigned: list[dict] = []
    unmatched: list[dict] = []
    repairs: list[dict] = []
    alternatives: dict[str, list[dict]] = {}
    list_sources: dict[str, str] = {}
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
        amount = None
        if package.package_amount and package.package_amount.amount_yuan is not None:
            amount = package.package_amount.amount_yuan
        elif single and understanding.summary_amount and understanding.summary_amount.amount_yuan is not None:
            amount = understanding.summary_amount.amount_yuan
        pool, owner = _select_cobs(items, unmatched, understanding.project_name)
        list_sources[package.package_no] = owner
        clusters = [_reconcile(group, conflicts, owner) for group in _clusters(pool, closed=owner == "html")]
        clusters = [item for item in clusters if _value(item.base, "object_name")]
        for swap in _swap_by_amount(clusters, amount, owner):
            repairs.append({"package_no": package.package_no, **swap})
        alternatives[package.project_id] = _alternatives(clusters)
        cobs = [_to_cob(item) for item in clusters]
        if len(cobs) == 1 and cobs[0].total_price is None and cobs[0].unit_price is None and amount is not None:
            cobs[0].total_price = amount
            repairs.append({"package_no": package.package_no, "action": "total_from_package_amount", "value": amount})
        subs, sub_ids, sub_conflicts, _ = _merge_entity(items, "sub")
        conflicts.extend(sub_conflicts)
        unique_products = list(dict.fromkeys(cob.product_supplier for cob in cobs if cob.product_supplier))
        suppliers = [
            Sub(
                supplier_name=sub.supplier_name,
                score=sub.score,
                is_winner=bool(sub.is_winner),
                cooperative_product_suppliers=unique_products if sub.is_winner else [],
            )
            for sub in subs
        ]
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
                provenance={
                    "cob_candidate_ids": [item.base.candidate_id for item in clusters],
                    "price_candidate_ids": [None if item.chosen is None else item.chosen.candidate_id for item in clusters],
                    "sub_candidate_ids": sub_ids,
                },
            )
        )
    open_conflicts = [item for item in conflicts if item["field"] != "price_bundle"]
    status = "partial" if open_conflicts or unassigned else "success"
    return MergedProjects(
        run_id=run_id,
        status=status,
        projects=projects,
        conflicts=conflicts,
        unmatched_summary_rows=unmatched,
        unassigned_candidates=unassigned,
        list_sources=list_sources,
        alternatives={key: value for key, value in alternatives.items() if value},
        repairs=repairs,
        failures=[],
    )


def _to_cob(cluster: _Cluster) -> Cob:
    base = cluster.base
    bundle = cluster.bundle
    return Cob(
        object_name=str(_value(base, "object_name")),
        category_code=_value(base, "category_code"),
        category_name=_value(base, "category_name"),
        category_type=_value(base, "category_type"),
        brand=_value(base, "brand"),
        product_supplier=_value(base, "product_supplier"),
        spec_model=_value(base, "spec_model"),
        unit_price=_number(bundle.get("unit_price")),
        quantity=_number(bundle.get("quantity")),
        unit=None if bundle.get("unit") in (None, "") else str(bundle.get("unit")),
        total_price=_number(bundle.get("total_price")),
    )


def _merge_entity(items: list[Candidate], entity_type: str):
    chosen: dict[tuple, Candidate] = {}
    order: list[tuple] = []
    conflicts: list[dict] = []
    unmatched: list[dict] = []
    keys = SUB_FIELDS
    for candidate in items:
        if candidate.entity_type != entity_type:
            continue
        key = (MEMBER_RE.sub("", _norm_name(_value(candidate, "supplier_name"))),)
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
        html_names = [item for item in (current, candidate) if item.source.source_type == "html"]
        if html_names:
            base.fields["supplier_name"].normalized_value = _value(html_names[0], "supplier_name")
        for field_key in keys:
            if field_key == "supplier_name":
                continue
            left = _value(base, field_key)
            right = _value(extra, field_key)
            if left in (None, "") and right not in (None, ""):
                base.fields[field_key].normalized_value = right
                base.fields[field_key].status = "present"
            elif field_key == "is_winner" and left in (None, False, "") and right in (True, "是", "true"):
                base.fields[field_key].normalized_value = right
                base.fields[field_key].status = "present"
            elif left not in (None, "") and right not in (None, "") and not _same(left, right) and field_key != "is_winner":
                base.fields[field_key].status = "conflict"
                conflicts.append(
                    {"field": field_key, "candidate_ids": [base.candidate_id, extra.candidate_id], "values": [left, right]}
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


def package_checks(project: Project, list_source: str | None) -> list[dict]:
    """Hard checks go to repair. Medium checks need review. Soft checks are only recorded."""
    checks: list[dict] = []
    package_no = project.package_no
    if not project.cobs:
        checks.append({"package_no": package_no, "check": "missing_cob", "level": "hard"})
    if not any(sub.is_winner for sub in project.subs):
        checks.append({"package_no": package_no, "check": "missing_winner", "level": "hard"})
    for index, cob in enumerate(project.cobs):
        if _row_bad({"unit_price": cob.unit_price, "quantity": cob.quantity, "total_price": cob.total_price}):
            checks.append({"package_no": package_no, "check": "row_mismatch", "level": "hard", "cob_index": index})
    totals = [cob.total_price for cob in project.cobs if isinstance(cob.total_price, (int, float))]
    amount = project.package_total_amount
    if amount and totals:
        summed = sum(totals)
        deviation = (summed - amount) / abs(amount)
        if deviation > AMOUNT_TOLERANCE:
            checks.append(
                {"package_no": package_no, "check": "amount_overflow", "level": "hard", "line_sum": summed, "package_total": amount}
            )
        elif deviation < -AMOUNT_TOLERANCE:
            checks.append(
                {
                    "package_no": package_no,
                    "check": "amount_underflow",
                    "level": "soft" if list_source == "html" else "medium",
                    "line_sum": summed,
                    "package_total": amount,
                }
            )
    return checks


def consistency_checks(merged: MergedProjects) -> list[Failure]:
    checks: list[dict] = []
    for project in merged.projects:
        checks.extend(package_checks(project, merged.list_sources.get(project.package_no)))
    merged.checks = checks
    blocking = [item for item in checks if item["level"] in {"hard", "medium"}]
    if not blocking:
        return []
    if merged.status == "success":
        merged.status = "partial"
    return [
        Failure(
            failure_code="consistency_check_failed",
            failure_message="；".join(f"{item['package_no']}:{item['check']}" for item in blocking)[:500],
        )
    ]
