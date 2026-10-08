"""Step 1. The model proposes package structure; code recomputes amounts and project ids."""

from __future__ import annotations

import re

from ict.html_context import ParsedNotice
from ict.ids import make_project_id
from ict.llm import LLMClient, LLMError
from ict.money import parse_amount
from ict.schemas import Amount, AnnouncementUnderstanding, Failure, ModelMetadata, PackageUnderstanding

PROMPT = "announcement-v1"


def _pack(text: str) -> str:
    return re.sub(r"\s+", "", text)


def checked_categories(raw_items, summary_text) -> tuple[list[str], list[str]]:
    """Keep only the 品目 entries the model transcribed from the announcement summary.

    公告概要里的品目只有这一个出口，而它的分隔符（逗号、顿号、分号、换行）没有稳定写法，切开
    这件事交给模型。规则只做结构校验：留下来的每一项都必须在它被抄的那段原文里连续出现，
    否则丢掉并报告——模型改写、补全或凭记忆写出来的条目进不到下游。
    """
    if not isinstance(raw_items, list):
        return [], []
    haystack = _pack(str(summary_text or ""))
    kept: list[str] = []
    dropped: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        text = str(item or "").strip()
        if not text:
            continue
        key = _pack(text)
        if not haystack or key not in haystack:
            dropped.append(text)
            continue
        if key in seen:                  # 原文自己会重复（如 C16990000,C16990000），不算抄错
            continue
        seen.add(key)
        kept.append(text)
    return kept, dropped


def _confidence(value, default: float = 0.9) -> float:
    """The model may write confidence as a word; it is an audit field, so never let it throw."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if 0 <= number <= 1 else default


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
        # Every other amount the model saw for this package, kept verbatim. Step 8 still receives
        # one package_amount; this only makes the choice it inherited visible. 22 of 49 packages
        # in the corpus state the amount in more than one place.
        alternatives: list[Amount] = []
        for entry in item.get("amount_alternatives") or []:
            if not isinstance(entry, dict):
                continue
            alt = _amount(entry.get("raw_text") or entry.get("raw_amount"), "package",
                          _confidence(entry.get("confidence")))
            if alt is not None:
                alternatives.append(alt)
        packages.append(
            PackageUnderstanding(
                package_no=package_no,
                title=item.get("title"),
                project_id=make_project_id(name, package_no) if name else "",
                package_evidence_text=evidence,
                package_amount=_amount(raw_amount, "package"),
                amount_alternatives=alternatives,
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
    categories = [str(item).strip() for item in (proposed.get("announcement_categories") or [])
                  if isinstance(item, str) and str(item).strip()]
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
        announcement_categories=categories,
        unclear_reason=proposed.get("unclear_reason"),
        model_metadata=metadata,
        failures=failures,
    )


def _validate_announcement(parsed: dict) -> str:
    mode = parsed.get("package_mode")
    if mode not in {"single", "multi", "unclear", None, ""}:
        return "package_mode 不在 single、multi、unclear 之中"
    kind = parsed.get("announcement_type")
    if kind not in {"winning_announcement", "deal_announcement", "unknown", None, ""}:
        return "announcement_type 不在允许值内"
    return ""


def understand(run_id: str, notice: ParsedNotice, llm: LLMClient | None, counter=None) -> AnnouncementUnderstanding:
    import json

    from ict.html_context import fill_package_amounts, step1_payload
    from ict.llm import complete_json

    failures: list[Failure] = []
    if llm is None:
        failures.append(Failure(failure_code="llm_call_failed", failure_message="未配置 DeepSeek"))
        return seal(run_id, {"package_mode": "unclear", "unclear_reason": "未配置 DeepSeek"}, None, failures)
    try:
        proposed = complete_json(
            llm,
            step="understand_announcement",
            prompt_version=PROMPT,
            user=json.dumps(step1_payload(notice), ensure_ascii=False),
            validate=_validate_announcement,
            counter=counter,
        )
    except (LLMError, ValueError) as exc:
        code = "llm_call_failed" if isinstance(exc, LLMError) else "llm_schema_invalid"
        failures.append(Failure(failure_code=code, failure_message=str(exc)))
        return seal(run_id, {"package_mode": "unclear", "unclear_reason": str(exc)}, None, failures)
    metadata = ModelMetadata(
        model_name=getattr(llm, "model", "configured"),
        model_version=getattr(llm, "model", "configured"),
        prompt_version=PROMPT,
        latency_ms=0,
    )
    if "summary_raw" not in proposed:
        summary = proposed.get("summary_amount") or {}
        proposed["summary_raw"] = summary.get("raw_text") if isinstance(summary, dict) else None
    kept, dropped = checked_categories(proposed.get("announcement_categories"),
                                       notice.summary.get("品目"))
    proposed["announcement_categories"] = kept
    for text in dropped:
        failures.append(Failure(failure_code="category_item_not_in_summary",
                                failure_message="公告概要里没有这一段：" + text))
    understanding = seal(run_id, proposed, metadata, failures)
    fill_package_amounts(understanding, notice)
    return understanding
