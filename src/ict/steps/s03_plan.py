"""Step 3. Gap planning is rule-based and does not call the model."""

from __future__ import annotations

from ict.config import COB_FIELDS, OFFICIAL_SEVEN
from ict.schemas import AnnouncementUnderstanding, Candidate, Failure, FieldStat, ProjectPlan, ProjectPlans


def _stats(cobs: list[Candidate]) -> dict[str, FieldStat]:
    total = len(cobs)
    stats: dict[str, FieldStat] = {}
    for key in COB_FIELDS:
        present = points = missing = 0
        for candidate in cobs:
            status = candidate.fields[key].status
            if status == "present":
                present += 1
            elif status == "points_to_attachment":
                points += 1
            elif status == "missing":
                missing += 1
        stats[key] = FieldStat(
            total=total,
            present=present,
            points_to_attachment=points,
            missing=missing,
            coverage=0 if total == 0 else present / total,
        )
    return stats


def plan_projects(
    run_id: str,
    understanding: AnnouncementUnderstanding,
    candidates: list[Candidate],
    has_attachment: bool,
) -> ProjectPlans:
    projects: list[ProjectPlan] = []
    failures: list[Failure] = []
    if not has_attachment:
        failures.append(Failure(failure_code="no_attachment", failure_message="当前公告没有附件目录"))
    for package in understanding.packages:
        cobs = [item for item in candidates if item.entity_type == "cob" and item.project_id == package.project_id]
        subs = [item for item in candidates if item.entity_type == "sub" and item.project_id == package.project_id]
        stats = _stats(cobs)
        category_types = set()
        for item in cobs:
            raw_type = item.fields["category_type"].normalized_value or item.fields["category_type"].raw_value
            raw_code = item.fields["category_code"].normalized_value or item.fields["category_code"].raw_value
            if raw_type:
                category_types.add(str(raw_type)[:1])
            if raw_code:
                category_types.add(str(raw_code)[:1])
        service_or_work = bool(category_types & {"B", "C"}) and not (category_types & {"A"})
        optional = {
            "brand",
            "spec_model",
            "product_supplier",
            "category_name",
            "category_code",
            "category_type",
            "unit_price",
            "quantity",
            "unit",
        } if service_or_work else set()
        missing = [
            key
            for key, stat in stats.items()
            if key not in optional
            and (stat.points_to_attachment or (key in OFFICIAL_SEVEN and stat.coverage < 0.3) or (key == "object_name" and stat.coverage < 1))
        ]
        missing = list(dict.fromkeys(missing))
        suspects: list[str] = []
        if any("html_table_is_summary" in item.issues or "summary_row" in item.issues for item in cobs):
            suspects.append("html_table_is_summary")
        if any("object_name_grain_suspect" in item.issues for item in cobs):
            suspects.append("object_name_grain_suspect")
        if any("partial_cob_list" in item.issues for item in cobs):
            suspects.append("partial_cob_list")
        if any("multi_value_cell" in item.issues for item in cobs):
            suspects.append("multi_value_cell")
        if any(item.package_no is None for item in candidates):
            suspects.append("package_scope_unknown")
        if any(stat.points_to_attachment for stat in stats.values()):
            suspects.append("field_points_to_attachment")
        if not cobs:
            suspects.append("no_cob_candidate")
        needs = has_attachment and (
            "no_cob_candidate" in suspects
            or "field_points_to_attachment" in suspects
            or "html_table_is_summary" in suspects
            or stats["object_name"].coverage < 1
            or any(key in OFFICIAL_SEVEN and key not in optional and stats[key].coverage < 0.3 for key in OFFICIAL_SEVEN)
        )
        names = [
            str(item.fields["object_name"].raw_value)
            for item in cobs
            if item.fields["object_name"].raw_value
        ][:5]
        queries = [f"第{package.package_no}包 分项报价"]
        queries.extend(names)
        targets: list[dict[str, str]] = []
        for key in missing:
            targets.append({"field": key,
                          "reason": "points_to_attachment" if stats[key].points_to_attachment else "coverage_low",
                          "package_no": package.package_no})
        has_package_amount = bool(package.package_amount and package.package_amount.amount_yuan is not None)
        has_summary_amount = bool(understanding.summary_amount and understanding.summary_amount.amount_yuan is not None)
        if not has_package_amount and not (understanding.package_mode == "single" and has_summary_amount):
            targets.append({"field": "package_total_amount", "reason": "absent", "package_no": package.package_no})
        if not any(item.fields["is_winner"].raw_value not in (None, "", False) or item.fields["is_winner"].normalized_value
                   for item in subs):
            targets.append({"field": "is_winner", "reason": "absent", "package_no": package.package_no})
        if not any(item.fields["score"].normalized_value is not None or item.fields["score"].raw_value not in (None, "")
                   for item in subs):
            targets.append({"field": "score", "reason": "absent", "package_no": package.package_no})
        # Standing target: the announcement's own object list is often one summary row plus
        # 详见附件, so attachments may still hold objects the announcement never named.
        targets.append({"field": "object_name", "reason": "maybe_more_objects",
                        "package_no": package.package_no})
        projects.append(
            ProjectPlan(
                project_id=package.project_id,
                package_no=package.package_no,
                cob_candidate_ids=[item.candidate_id for item in cobs],
                sub_candidate_ids=[item.candidate_id for item in subs],
                field_stats=stats,
                missing_fields=missing,
                needs=targets,
                suspects=suspects,
                has_attachment=has_attachment,
                needs_attachment=needs,
                search_queries=queries,
                status="success",
            )
        )
    status = "partial" if failures or any(item.needs_attachment for item in projects) else "success"
    return ProjectPlans(run_id=run_id, status=status, projects=projects, failures=failures)
