"""Seal model observations into the existing candidate/evidence interface."""
from ict.candidates import seal_candidate
from ict.config import CLASS_PRIORITY, SOURCE_PRIORITY
from ict.evidence import resolve_context
from ict.ids import make_project_id
from ict.money import parse_amount
from ict.schemas import PackageAmountObservation


def seal_observations(rows, contexts, project_name, seq, *, package=None):
    candidates = []
    for row in rows:
        context = resolve_context(row, contexts)
        ref = row.get("source_ref")
        segment = next(item for item in context["source_segments"] if item["source_ref"] == ref)
        number = package.package_no if package else row.get("package_no")
        source_class = row.get("source_class", context.get("kind", "unknown"))
        candidates.append(seal_candidate(
            candidate_id="cand_%06d" % seq, entity_type=row["entity_type"],
            package_no=number, project_id=make_project_id(project_name, number) if number else None,
            source_type=context.get("source_type", "html"), file_id=context.get("file_id"),
            page_no=context.get("page_no"), source_class=source_class,
            source_priority=(SOURCE_PRIORITY if context.get("source_type") == "html" else CLASS_PRIORITY).get(source_class, 30),
            raw_fields=row["fields"], issues=row.get("issues", []),
            table_index=segment.get("table_index"), row_index=segment.get("row_index"),
            row_text=segment.get("text"), column_units=row.get("column_units"),
            source_refs=[ref], quote_supplier=row.get("quote_supplier"),
            bidder_supplier=row.get("bidder_supplier"), winner_supplier=row.get("winner_supplier")))
        seq += 1
    return candidates, seq


def seal_amounts(rows, contexts):
    amounts = []
    for row in rows:
        context = resolve_context(row, contexts)
        value, _ = parse_amount(row["raw_text"])
        if row.get("unit_header"):
            from ict.money import parse_price_cell
            value = parse_price_cell(row["raw_text"], row["unit_header"])["value"]
        amounts.append(PackageAmountObservation(
            package_no=row.get("package_no"), raw_text=row["raw_text"], amount_yuan=value,
            source_type=context.get("source_type", "pdf"), file_id=context["file_id"],
            file_name=context.get("file_name"), page_no=context.get("page_no"),
            file_class=row.get("source_class", context.get("kind", "unknown")),
            amount_kind=row.get("amount_kind", "unknown"), source_ref=row["source_ref"]))
    return amounts
