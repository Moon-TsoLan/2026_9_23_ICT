"""Step 7. Normalize prices, quantities, and catalog hits. The model is not called."""

from __future__ import annotations

import re

from ict.catalog import Catalog
from ict.money import parse_price_cell, parse_quantity
from ict.schemas import Candidate, CandidateFile, Failure

PRICE_FIELDS = ("unit_price", "total_price")
# Half-width applies to the round brackets and the ideographic space only. The enumeration comma,
# the colon and the semicolon are left alone on purpose: names such as 「触控一体机6台、教师办公
# 电脑14台」 carry meaning in that punctuation, and a tenth of the corpus's spec_model values are
# multi-word model numbers where a space between latin tokens is content (`BeneVision TMS30A`).
WIDTH_MAP = {"（": "(", "）": ")", "　": " ", "\u00a0": " "}
_CJK = "㐀-䶿一-鿿豈-﫿"


def norm_text(value) -> str:
    """The conservative text cleaning step 7 owed its fields: typesetting noise, nothing else.

    Case stays as written, a space between two latin or digit tokens stays, and the enumeration
    punctuation stays. What goes is full-width brackets and the padding around CJK - which is what
    made one document's 「（1 拖 40）」 and another's 「（1拖40）」 read as a disagreement about
    the same object, and pushed a package that matches gold into review.
    """
    text = re.sub(r"\s+", " ", str(value).translate(str.maketrans(WIDTH_MAP))).strip()
    text = re.sub("(?<=[" + _CJK + "]) +", "", text)
    text = re.sub(" +(?=[" + _CJK + "])", "", text)
    return text.strip()


def _number(value) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def normalize_candidates(run_id: str, candidates: list[Candidate], catalog: Catalog) -> CandidateFile:
    failures: list[Failure] = []
    for candidate in candidates:
        errors: list[str] = []
        warnings: list[str] = []
        if candidate.entity_type == "cob":
            _normalize_cob(candidate, catalog, errors, warnings)
        else:
            _normalize_sub(candidate, errors)
        candidate.validation_errors = errors
        candidate.warnings = warnings
        if errors:
            failures.append(
                Failure(
                    failure_code="field_normalization_failed",
                    failure_message=";".join(errors),
                    location=candidate.candidate_id,
                )
            )
    status = "partial" if failures else "success"
    if not candidates:
        status = "failed"
    return CandidateFile(run_id=run_id, status=status, candidates=candidates, failures=failures)


def _normalize_cob(candidate: Candidate, catalog: Catalog, errors: list[str], warnings: list[str]) -> None:
    fields = candidate.fields
    units = (candidate.evidence.column_units if candidate.evidence is not None else None) or {}
    for key in PRICE_FIELDS:
        obs = fields[key]
        if obs.status != "present":
            continue
        parsed = parse_price_cell(obs.raw_value, units.get(key))
        obs.normalization = {
            "currency": "CNY",
            "unit": "yuan",
            "multiplied_by_10000": parsed["multiplied_by_10000"],
        }
        if parsed["ambiguous"]:
            errors.append("ambiguous_price_unit")
            obs.status = "low_confidence"
        elif parsed["invalid"]:
            errors.append("invalid_number")
        elif parsed["value"] is not None and parsed["value"] < 0:
            errors.append("negative_price_unexpected")
            obs.normalized_value = parsed["value"]
        else:
            obs.normalized_value = parsed["value"]
    qty = fields["quantity"]
    unit = fields["unit"]
    if qty.status == "present":
        number, parsed_unit, ok = parse_quantity(qty.raw_value)
        if not ok:
            errors.append("quantity_unit_parse_failed")
        else:
            qty.normalized_value = number
            if parsed_unit and unit.status == "missing":
                unit.raw_value = parsed_unit
                unit.normalized_value = parsed_unit
                unit.status = "present"
    if unit.status == "present" and unit.normalized_value is None:
        unit.normalized_value = norm_text(unit.raw_value)
    price = _number(fields["unit_price"].normalized_value)
    quantity = _number(fields["quantity"].normalized_value)
    total = fields["total_price"]
    if total.status != "present" and price is not None and quantity is not None:
        total.normalized_value = round(price * quantity, 4)
        total.status = "present"
        total.normalization = {"filled_from": "unit_price*quantity"}
    elif total.status == "present" and price is not None and quantity is not None:
        expected = price * quantity
        actual = _number(total.normalized_value)
        if actual is not None and expected and abs(actual - expected) / abs(expected) > 0.05:
            warnings.append("total_price_mismatch")
    name = fields["object_name"]
    if name.status != "present":
        errors.append("missing_object_name")
    else:
        name.normalized_value = norm_text(name.raw_value)
    for key in ("brand", "product_supplier", "spec_model", "category_name", "category_code"):
        obs = fields[key]
        if obs.status == "present" and isinstance(obs.raw_value, str):
            obs.normalized_value = norm_text(obs.raw_value) or None
    code = fields["category_code"].normalized_value or fields["category_code"].raw_value
    cname = fields["category_name"].normalized_value or fields["category_name"].raw_value
    hit = catalog.match(None if code is None else str(code), None if cname is None else str(cname))
    fields["category_code"].normalization = hit
    if hit.get("match_type") == "unmatched":
        if code:
            errors.append("category_code_not_found")
    elif hit.get("matched_code"):
        if fields["category_code"].status == "missing":
            fields["category_code"].normalized_value = hit["matched_code"]
            fields["category_code"].status = "present"
        if fields["category_name"].status == "missing":
            fields["category_name"].normalized_value = hit["matched_name"]
            fields["category_name"].status = "present"
        if hit.get("conflict"):
            errors.append("category_conflict")
        if fields["category_type"].status == "missing" and hit.get("category_type"):
            fields["category_type"].normalized_value = hit["category_type"]
            fields["category_type"].status = "present"
    if fields["category_type"].status == "present" and fields["category_type"].normalized_value is None:
        raw_type = str(fields["category_type"].raw_value)
        if raw_type[:1] in {"A", "B", "C"}:
            fields["category_type"].normalized_value = raw_type[:1]
        elif "货物" in raw_type:
            fields["category_type"].normalized_value = "A"
        elif "工程" in raw_type:
            fields["category_type"].normalized_value = "B"
        elif "服务" in raw_type:
            fields["category_type"].normalized_value = "C"
    if hit.get("match_type") == "fuzzy":
        warnings.append("fuzzy")


def _normalize_sub(candidate: Candidate, errors: list[str]) -> None:
    name = candidate.fields["supplier_name"]
    if name.status != "present" or not str(name.raw_value or "").strip():
        errors.append("missing_supplier_name")
    else:
        name.normalized_value = str(name.raw_value).strip()
    score = candidate.fields["score"]
    if score.status == "present":
        try:
            score.normalized_value = float(str(score.raw_value).replace(",", ""))
        except ValueError:
            errors.append("invalid_number")
            score.normalized_value = None
            score.status = "missing"
    winner = candidate.fields["is_winner"]
    if winner.status == "present":
        raw = winner.raw_value
        winner.normalized_value = raw if isinstance(raw, bool) else str(raw).strip() in {"true", "True", "是", "中标", "1"}
    else:
        winner.normalized_value = False
        winner.status = "present"
