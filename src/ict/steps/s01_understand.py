"""Step 1. The model proposes package structure; code recomputes amounts and project ids."""

from __future__ import annotations

from ict.html_context import ParsedNotice
from ict.ids import make_project_id
from ict.llm import LLMClient, LLMError, parse_json_object
from ict.money import parse_amount
from ict.schemas import Amount, AnnouncementUnderstanding, Failure, ModelMetadata, PackageUnderstanding

PROMPT = "announcement-v1"


def _amount(raw: str | None, scope: str, confidence: float | None = 0.9) -> Amount | None:
    if not raw:
        return None
    yuan, _ = parse_amount(raw)
    return Amount(raw_text=raw, amount_yuan=yuan, scope=scope, confidence=confidence)


def seal(run_id: str, proposed: dict, metadata: ModelMetadata | None, failures: list[Failure]) -> AnnouncementUnderstanding:
    name = (proposed.get("project_name") or "").strip()
    mode = proposed.get("package_mode") or "unclear"
    raw_packages = proposed.get("packages") or []
    packages: list[PackageUnderstanding] = []
    seen: set[str] = set()
    if not name:
        failures.append(Failure(failure_code="package_structure_unclear", failure_message="项目名称缺失"))
        mode = "unclear"
    for item in raw_packages:
        package_no = str(item.get("package_no") or "").strip()
        if not package_no or package_no in seen:
            continue
        seen.add(package_no)
        evidence = item.get("package_evidence_text") or item.get("title") or package_no
        raw_amount = item.get("raw_amount") or (item.get("package_amount") or {}).get("raw_text")
        packages.append(
            PackageUnderstanding(
                package_no=package_no,
                title=item.get("title"),
                project_id=make_project_id(name, package_no) if name else "",
                package_evidence_text=evidence,
                package_amount=_amount(raw_amount, "package"),
            )
        )
    if mode == "single" and not packages and name:
        packages = [
            PackageUnderstanding(
                package_no="1",
                title=name,
                project_id=make_project_id(name, "1"),
                package_evidence_text=name,
                package_amount=None,
            )
        ]
    if mode == "single" and len(packages) != 1:
        mode = "unclear"
        failures.append(Failure(failure_code="package_structure_unclear", failure_message="单包公告的包数量不是 1"))
    if mode == "multi" and len(packages) < 2:
        mode = "unclear"
        failures.append(Failure(failure_code="package_structure_unclear", failure_message="多包公告少于两个包"))
    if mode == "unclear" and not any(item.failure_code == "package_structure_unclear" for item in failures):
        failures.append(Failure(failure_code="package_structure_unclear", failure_message=proposed.get("unclear_reason")))
    status = "failed" if mode == "unclear" or not name else "success"
    if failures and status == "success":
        status = "partial"
    return AnnouncementUnderstanding(
        run_id=run_id,
        status=status,
        project_name=name or None,
        purchaser=proposed.get("purchaser"),
        source_project_no=proposed.get("source_project_no"),
        announcement_type=proposed.get("announcement_type") or "unknown",
        package_mode=mode if mode in {"single", "multi", "unclear"} else "unclear",
        packages=[] if status == "failed" and not name else packages,
        summary_amount=_amount(proposed.get("summary_raw"), "announcement"),
        unclear_reason=proposed.get("unclear_reason"),
        model_metadata=metadata,
        failures=failures,
    )


def understand(run_id: str, notice: ParsedNotice, llm: LLMClient | None) -> AnnouncementUnderstanding:
    import json

    from ict.html_context import step1_payload

    failures: list[Failure] = []
    if llm is None:
        failures.append(Failure(failure_code="llm_call_failed", failure_message="未配置 DeepSeek"))
        return seal(run_id, {"package_mode": "unclear", "unclear_reason": "未配置 DeepSeek"}, None, failures)
    try:
        result = llm.complete(
            step="understand_announcement",
            prompt_version=PROMPT,
            user=json.dumps(step1_payload(notice), ensure_ascii=False),
        )
    except LLMError as exc:
        failures.append(Failure(failure_code="llm_call_failed", failure_message=str(exc)))
        return seal(run_id, {"package_mode": "unclear", "unclear_reason": str(exc)}, None, failures)
    metadata = ModelMetadata(
        model_name=result.model_name,
        model_version=result.model_version,
        prompt_version=result.prompt_version,
        latency_ms=result.latency_ms,
    )
    try:
        proposed = parse_json_object(result.text)
    except ValueError as exc:
        failures.append(Failure(failure_code="llm_schema_invalid", failure_message=str(exc)))
        return seal(run_id, {"package_mode": "unclear"}, metadata, failures)
    if "summary_raw" not in proposed:
        summary = proposed.get("summary_amount") or {}
        proposed["summary_raw"] = summary.get("raw_text") if isinstance(summary, dict) else None
    return seal(run_id, proposed, metadata, failures)
