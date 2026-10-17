"""Independent attachment output channels; never used to splice dependent delta plans."""
from copy import deepcopy
import json

from ict.evidence import resolve_context
from ict.llm import ValidationIssue
from ict.config import COB_FIELDS, SUB_FIELDS
from ict.schemas import FieldObservation


class ExtractionReceiver:
    def __init__(self, contexts, needs=None):
        self.contexts = contexts
        self.needs = None if needs is None else {item.get("need_id"): item for item in needs}
        self.received = False
        self.outputs = {"candidates": [], "package_amounts": [], "need_resolutions": []}
        self.seen = {key: set() for key in self.outputs}
        self.audit = []

    def accept(self, parsed, attempt):
        self.received = True
        issues = []
        for channel in self.outputs:
            rows = parsed.get(channel, [])
            if not isinstance(rows, list):
                issue = ValidationIssue("需要数组", path=channel)
                issues.append(issue)
                self.audit.append({"attempt": attempt + 1, **issue.as_dict(), "raw": deepcopy(rows)})
                continue
            # Deduplicate re-emission across repair attempts, not independent same-response rows.
            previous = set(self.seen[channel])
            for index, row in enumerate(rows):
                error = self.check(channel, row)
                if error:
                    issue = ValidationIssue(error, path=f"{channel}[{index}]")
                    issues.append(issue)
                    self.audit.append({"attempt": attempt + 1, **issue.as_dict(), "raw": deepcopy(row)})
                    continue
                fingerprint = json.dumps(row, sort_keys=True, ensure_ascii=False)
                if fingerprint not in previous:
                    self.outputs[channel].append(deepcopy(row))
                self.seen[channel].add(fingerprint)
        return issues

    def check(self, channel, row):
        if not isinstance(row, dict):
            return "每项必须为对象"
        if resolve_context(row, self.contexts) is None:
            return "每项必须引用唯一来源片段，文件与页码必须一致"
        if channel == "candidates":
            if row.get("entity_type") not in ("cob", "sub"):
                return "entity_type 必须为 cob 或 sub"
            if not isinstance(row.get("fields"), dict):
                return "fields 必须是对象"
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
            if not isinstance(row.get("package_no"), str) or not row["package_no"].strip() or not isinstance(row.get("raw_text"), str) or not row["raw_text"].strip():
                return "package_amounts 每项需要 package_no 与 raw_text；未归属金额保留在接收审计，不能猜为公告金额"
            if row.get("scope", "package") != "package":
                return "附件金额业务通道只接收包金额，项目预算只留接收审计"
            if row.get("amount_kind", "unknown") not in ("award", "budget", "ceiling", "unknown"):
                return "amount_kind 无效"
        elif not all(isinstance(row.get(key), str) and row[key] for key in
                     ("need_id", "field", "target_candidate_id", "source_ref")):
            return "need_resolutions 需要 need_id、field、target_candidate_id、source_ref"
        elif row.get("source_candidate_ref") is not None and not isinstance(row["source_candidate_ref"], str):
            return "source_candidate_ref 必须为唯一来源片段引用字符串"
        elif self.needs is not None:
            need = self.needs.get(row["need_id"])
            if (need is None or need.get("purpose") != "fill_field" or need.get("field") != row["field"]
                    or row["target_candidate_id"] not in need.get("target_candidate_ids", [])):
                return "只能解决输入fill_field任务给出的真实旧目标；新对象输出为候选，包金额走独立通道"
        return ""

    def result(self, attempts):
        return {**deepcopy(self.outputs), "reception": {"attempts": deepcopy(attempts),
                "rejected_items": deepcopy(self.audit),
                "unrepaired": bool(attempts and attempts[-1].get("issues")),
                "accepted_counts": {key: len(rows) for key, rows in self.outputs.items()}}}
