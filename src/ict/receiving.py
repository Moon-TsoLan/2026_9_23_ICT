"""Independent attachment output channels; never used to splice dependent delta plans."""
from copy import deepcopy
import json

from ict.evidence import resolve_context
from ict.llm import ValidationIssue
from ict.config import COB_FIELDS, SUB_FIELDS
from ict.fields import FRONT_ENUMS
from ict.schemas import FieldObservation


class ExtractionReceiver:
    def __init__(self, contexts):
        self.contexts = contexts
        self.received = False
        self.outputs = {"candidates": [], "package_amounts": []}
        self.seen = {key: set() for key in self.outputs}
        self.audit = []
        self.pending = {}
        self.metadata = {}

    def accept(self, parsed, attempt):
        self.received = True
        self.metadata = {key: parsed[key] for key in ("kind", "confidence", "reason") if key in parsed}
        issues = []
        for channel in self.outputs:
            rows = parsed.get(channel, [])
            if not isinstance(rows, list):
                issue = ValidationIssue("需要数组", path=channel)
                issues.append(issue)
                self.audit.append({"attempt": attempt + 1, **issue.as_dict(), "raw": deepcopy(rows)})
                self.pending[issue.path] = self.audit[-1]
                continue
            self.pending.pop(channel, None)
            # Deduplicate re-emission across repair attempts, not independent same-response rows.
            previous = set(self.seen[channel])
            for index, row in enumerate(rows):
                error = self.check(channel, row)
                if error:
                    issue = (ValidationIssue(error.message, path=f"{channel}[{index}].{error.path}")
                             if isinstance(error, ValidationIssue) else ValidationIssue(error, path=f"{channel}[{index}]"))
                    issues.append(issue)
                    self.audit.append({"attempt": attempt + 1, **issue.as_dict(), "raw": deepcopy(row)})
                    self.pending[issue.path] = self.audit[-1]
                    continue
                path = f"{channel}[{index}]"
                self.pending = {key: item for key, item in self.pending.items()
                                if key != path and not key.startswith(path + ".")}
                fingerprint = json.dumps(row, sort_keys=True, ensure_ascii=False)
                if fingerprint not in previous:
                    self.outputs[channel].append(deepcopy(row))
                self.seen[channel].add(fingerprint)
        return issues

    def check(self, channel, row):
        if not isinstance(row, dict):
            return "每项必须为对象"
        if not isinstance(row.get("source_ref"), str):
            return "每项需要输入提供的 source_ref"
        if resolve_context(row, self.contexts) is None:
            return "每项必须引用唯一来源片段，文件与页码必须一致"
        if channel == "candidates":
            if row.get("entity_type") not in FRONT_ENUMS["entity_type"]:
                return ValidationIssue(f"原值={row.get('entity_type')!r}；允许值={FRONT_ENUMS['entity_type']}", path="entity_type")
            if not isinstance(row.get("fields"), dict):
                return "fields 必须是对象"
            if row.get("source_class", "unknown") not in FRONT_ENUMS["source_class"]:
                return ValidationIssue(f"原值={row.get('source_class')!r}；允许值={FRONT_ENUMS['source_class']}", path="source_class")
            if not isinstance(row.get("column_units", {}), dict) or any(
                    not isinstance(value, str) for value in row.get("column_units", {}).values()):
                return "column_units 必须为字段名到原文表头的对象"
            for key, value in row["fields"].items():
                if key not in (COB_FIELDS if row["entity_type"] == "cob" else SUB_FIELDS):
                    continue
                if isinstance(value, dict):
                    try:
                        FieldObservation.model_validate(value)
                    except ValueError:
                        return f"fields.{key} 的观测结构无效"
                elif value is not None and not isinstance(value, (str, int, float, bool)):
                    return f"fields.{key} 需要原文标量或完整字段观测"
            if row.get("issues") is not None and (not isinstance(row["issues"], list) or
                    any(not isinstance(issue, str) for issue in row["issues"])):
                return "issues 必须为字符串数组"
            if row.get("package_no") is not None and not isinstance(row["package_no"], str):
                return "package_no 必须为字符串或 null"
        elif channel == "package_amounts":
            if row.get("unit_header") is not None and not isinstance(row["unit_header"], str):
                return "unit_header 必须为原文表头字符串或 null"
            if row.get("package_no") is not None and (not isinstance(row["package_no"], str) or not row["package_no"].strip()):
                return "package_no 必须为目录引用字符串或null；归属不明时留空交包归属组件"
            if not isinstance(row.get("raw_text"), str) or not row["raw_text"].strip():
                return "package_amounts 每项需要非空金额原文raw_text"
            if row.get("scope", "package") != "package":
                return "附件金额业务通道只接收包金额，项目预算只留接收审计"
            if row.get("amount_kind", "unknown") not in FRONT_ENUMS["amount_kind"]:
                return ValidationIssue(f"原值={row.get('amount_kind')!r}；允许值={FRONT_ENUMS['amount_kind']}", path="amount_kind")
        return ""

    def result(self, attempts):
        return {**deepcopy(self.outputs), **deepcopy(self.metadata), "reception": {"attempts": deepcopy(attempts),
                "rejected_items": deepcopy(self.audit),
                "unrepaired": bool(self.pending or attempts and attempts[-1].get("issues")),
                "unresolved_items": list(deepcopy(self.pending).values()),
                "accepted_counts": {key: len(rows) for key, rows in self.outputs.items()}}}
