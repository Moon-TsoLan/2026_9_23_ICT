"""Candidate field shells. Every entity key is present; missing stays missing."""

from __future__ import annotations

from typing import Any

from ict.config import COB_FIELDS, SUB_FIELDS
from ict.schemas import Candidate, FieldObservation, Source

POINTING = ("详见附件", "详见招标文件", "详见磋商文件", "详见采购文件", "详见投标文件", "见附件")


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
) -> Candidate:
    fields = blank_fields(entity_type)
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
    )
