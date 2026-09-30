"""Candidate field shells. Every entity key is present; missing stays missing."""

from __future__ import annotations

from typing import Any

from ict.config import COB_FIELDS, SUB_FIELDS
from ict.schemas import Candidate, FieldObservation, Source

POINTING = ("详见附件", "见附件")
FIELD_ALIASES = {
    "cob": {
        "item_name": "object_name",
        "product_name": "object_name",
        "goods_name": "object_name",
        "object": "object_name",
        "name": "object_name",
        "产品名称": "object_name",
        "货物名称": "object_name",
        "标的名称": "object_name",
        "采购标的": "object_name",
        "报价明细内容": "object_name",
        "名称": "object_name",
        "brand_name": "brand",
        "品牌": "brand",
        "model": "spec_model",
        "specification": "spec_model",
        "规格型号": "spec_model",
        "型号": "spec_model",
        "manufacturer": "product_supplier",
        "制造商": "product_supplier",
        "生产厂家": "product_supplier",
        "数量": "quantity",
        "单价": "unit_price",
        "总价": "total_price",
        "金额": "total_price",
        "单位": "unit",
        "品目名称": "category_name",
        "品目编号及品目名称": "category_name",
        "品目编号": "category_code",
    },
    "sub": {
        "bidder_name": "supplier_name",
        "supplier": "supplier_name",
        "name": "supplier_name",
        "供应商名称": "supplier_name",
        "供应商": "supplier_name",
        "综合得分": "score",
        "评审总得分": "score",
        "是否中标": "is_winner",
    },
}


def align_fields(entity_type: str, raw_fields: dict[str, Any]) -> dict[str, Any]:
    """Map common wrong field names onto the contract keys. Known keys win."""
    aligned = dict(raw_fields)
    aliases = FIELD_ALIASES.get(entity_type, {})
    for source, target in aliases.items():
        if source not in raw_fields or target in aligned and aligned[target] not in (None, ""):
            continue
        aligned[target] = raw_fields[source]
    brand_model = raw_fields.get("brand_model") or raw_fields.get("品牌/型号")
    if brand_model not in (None, ""):
        if aligned.get("brand") in (None, ""):
            aligned["brand"] = brand_model
        if aligned.get("spec_model") in (None, "") and any(character.isdigit() for character in str(brand_model)):
            aligned["spec_model"] = brand_model
    return aligned


def blank_fields(entity_type: str) -> dict[str, FieldObservation]:
    keys = COB_FIELDS if entity_type == "cob" else SUB_FIELDS
    return {
        key: FieldObservation(raw_value=None, normalized_value=None, status="missing", confidence=None)
        for key in keys
    }


def observe(raw: Any, confidence: float | None = None) -> FieldObservation:
    if raw is None:
        return FieldObservation(raw_value=None, normalized_value=None, status="missing")
    text = raw if not isinstance(raw, str) else raw.strip()
    if text == "" or text in {"无", "-", "/"}:
        return FieldObservation(raw_value=None, normalized_value=None, status="missing", confidence=confidence)
    if isinstance(text, str) and any(token in text for token in POINTING) and len(text) < 12:
        return FieldObservation(
            raw_value=text, normalized_value=None, status="points_to_attachment", confidence=confidence
        )
    return FieldObservation(raw_value=text, normalized_value=None, status="present", confidence=confidence)


def seal_candidate(
    *,
    candidate_id: str,
    entity_type: str,
    project_id: str | None,
    package_no: str | None,
    source_type: str,
    file_id: str | None,
    source_priority: int,
    raw_fields: dict[str, Any],
    issues: list[str] | None = None,
    confidence: float | None = 0.8,
    source_class: str | None = None,
) -> Candidate:
    fields = blank_fields(entity_type)
    raw_fields = align_fields(entity_type, raw_fields)
    for key, raw in raw_fields.items():
        if key in fields:
            if isinstance(raw, dict) and "status" in raw:
                fields[key] = FieldObservation(**raw)
            else:
                fields[key] = observe(raw, confidence)
    return Candidate(
        candidate_id=candidate_id,
        entity_type=entity_type,
        project_id=project_id,
        package_no=package_no,
        source=Source(source_type=source_type, file_id=file_id),
        source_priority=source_priority,
        fields=fields,
        issues=issues or [],
        source_class=source_class,
    )
