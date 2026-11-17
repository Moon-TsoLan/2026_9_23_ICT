"""Receive one announcement answer: a fixed directory and independent observations."""
from copy import deepcopy
import json

from pydantic import ValidationError

from ict.fields import FRONT_ENUMS
from ict.llm import ValidationIssue
from ict.receiving import ExtractionReceiver
from ict.schemas import HtmlTable


IDENTITY = ("project_name", "purchaser", "source_project_no", "announcement_type")
UNIT = ("source_package_no", "package_kind", "package_name", "package_evidence_text", "title")


def invalid(path, value, expected):
    return ValidationIssue(f"原值={value!r}；应为{expected}", path=path)


def parent_error(value, branch):
    if not isinstance(value, dict):
        return invalid("$", value, "JSON对象")
    for key in IDENTITY + ("unclear_reason",):
        item = value.get(key)
        if item is not None and not isinstance(item, str):
            return invalid(key, item, "字符串或null")
    for key in ("announcement_type",):
        if value.get(key) not in (None, "", *FRONT_ENUMS[key]):
            return invalid(key, value[key], str(FRONT_ENUMS[key]))
    if branch == "summary":
        return None
    mode, packages = value.get("package_mode"), value.get("packages", [])
    if mode not in FRONT_ENUMS["package_mode"]:
        return invalid("package_mode", mode, str(FRONT_ENUMS["package_mode"]))
    if not isinstance(packages, list) or any(not isinstance(row, dict) for row in packages):
        return invalid("packages", packages, "采购单元对象数组")
    if mode == "single" and len(packages) != 1 or mode == "multi" and len(packages) < 2:
        return invalid("packages", packages, "与package_mode一致的采购单元目录；程序不补造单元")
    if value.get("package_no") is not None:
        return invalid("package_no", value["package_no"], "不输出；内部引用由程序分配")
    for index, package in enumerate(packages):
        root = f"packages[{index}]"
        if package.get("package_no") is not None:
            return invalid(root + ".package_no", package["package_no"], "不输出；原编号只写source_package_no")
        for key in UNIT:
            item = package.get(key)
            if item is not None and not isinstance(item, str):
                return invalid(root + "." + key, item, "字符串或null")
        if package.get("package_kind", "unknown") not in FRONT_ENUMS["package_kind"]:
            return invalid(root + ".package_kind", package["package_kind"], str(FRONT_ENUMS["package_kind"]))
    return None


class BodyCandidates(ExtractionReceiver):
    def __init__(self, contexts, refs):
        super().__init__(contexts)
        self.outputs = {"candidates": []}
        self.refs = refs

    def check(self, channel, row):
        if isinstance(row, dict) and row.get("package_no") is not None:
            return "不输出package_no；按目录嵌套，归属不明放顶层candidates"
        error = super().check(channel, row)
        if error:
            return error
        if row["source_ref"] not in self.refs:
            return "source_ref必须引用本分支提供的原文"
        return ""


class AnnouncementReceiver:
    """Local to one two-attempt call. No file/need lifecycle or cross-step state."""
    def __init__(self, branch, contexts, refs, table_indices):
        self.branch, self.contexts, self.refs = branch, contexts, refs
        self.table_indices = table_indices
        self.received = False
        self.parent = None
        self.children, self.values, self.seen = {}, {}, {}
        self.audit, self.pending = [], {}

    def directory(self, value):
        keys = IDENTITY + (("package_mode", "unclear_reason") if self.branch == "body" else ())
        parent = {key: value.get(key) for key in keys}
        if self.branch == "body":
            parent["packages"] = [{key: row.get(key) for key in UNIT} for row in value.get("packages", [])]
            for package in parent["packages"]:
                package["package_kind"] = package["package_kind"] or "unknown"
        return parent

    def validate(self, value):
        error = parent_error(value, self.branch)
        if error:
            return error
        if self.received and self.directory(value) != self.parent:
            return invalid("packages" if self.branch == "body" else "$", self.directory(value),
                           "沿用首次已接收的身份与目录、顺序；只修复独立观测，不重新分配采购单元")
        return None

    def reject(self, issue, raw, attempt):
        record = {"attempt": attempt + 1, **issue.as_dict(), "raw": deepcopy(raw)}
        self.audit.append(record)
        self.pending[issue.path] = record
        return issue

    def amount_error(self, value, path, scope):
        if not isinstance(value, dict):
            return invalid(path, value, "金额对象或null")
        kind = value.get("amount_kind", "unknown")
        if kind not in FRONT_ENUMS["amount_kind"]:
            return invalid(path + ".amount_kind", kind, str(FRONT_ENUMS["amount_kind"]))
        if value.get("scope", scope) != scope:
            return invalid(path + ".scope", value.get("scope"), scope)
        for key in ("raw_text", "unit_header"):
            if value.get(key) is not None and not isinstance(value[key], str):
                return invalid(path + "." + key, value[key], "原文字符串或null")
        if value.get("raw_text") and (not isinstance(value.get("source_ref"), str) or value["source_ref"] not in self.refs):
            return invalid(path + ".source_ref", value.get("source_ref"), "本分支实际给出的原文引用")
        return None

    def table_error(self, value, path):
        if not isinstance(value, dict):
            return invalid(path, value, "表注释对象")
        try:
            HtmlTable(**(value | {"package_scope": "announcement", "status": "success"}))
        except ValidationError as exc:
            error = exc.errors()[0]
            return invalid(path + "." + ".".join(map(str, error["loc"])), error.get("input"), error["msg"])
        if value["table_index"] not in self.table_indices:
            return invalid(path + ".table_index", value["table_index"], "正文中实际提供的表编号")
        return None

    def collect(self, key, rows, attempt, check, *, scalar=False):
        if not isinstance(rows, list):
            return [self.reject(invalid(key, rows, "数组"), rows, attempt)]
        self.pending.pop(key, None)
        accepted = self.values.setdefault(key, [])
        seen = self.seen.setdefault(key, set())
        previous = set(seen)
        issues = []
        for index, row in enumerate(rows):
            path = key if scalar else f"{key}[{index}]"
            error = check(row, path)
            if error:
                issues.append(self.reject(error, row, attempt))
                continue
            for old in list(self.pending):
                if old == path or old.startswith(path + "."):
                    self.pending.pop(old)
            fingerprint = json.dumps(row, ensure_ascii=False, sort_keys=True)
            if fingerprint not in previous:
                accepted.append(deepcopy(row))
            seen.add(fingerprint)
        return issues

    def candidates(self, key, rows, attempt):
        child = self.children.setdefault(key, BodyCandidates(self.contexts, self.refs))
        start = len(child.audit)
        issues = child.accept({"candidates": rows}, attempt)
        errors = []
        for issue, record in zip(issues, child.audit[start:]):
            path = key + issue.path[len("candidates"):]
            errors.append(self.reject(ValidationIssue(issue.message, path=path), record["raw"], attempt))
        if isinstance(rows, list):
            for index, row in enumerate(rows):
                if not child.check("candidates", row):
                    path = f"{key}[{index}]"
                    self.pending = {old: item for old, item in self.pending.items()
                                    if old != path and not old.startswith(path + ".")}
            self.pending.pop(key, None)
        return errors

    def accept(self, value, attempt):
        if not self.received:
            self.parent = deepcopy(self.directory(value))
            self.received = True
        issues = []
        if self.branch == "summary":
            if value.get("summary_amount") is not None:
                issues += self.collect("summary_amount", [value["summary_amount"]], attempt,
                    lambda row, path: self.amount_error(row, path, "announcement"), scalar=True)
            issues += self.collect("announcement_categories", value.get("announcement_categories", []), attempt,
                lambda row, path: None if isinstance(row, str) else invalid(path, row, "品目原文字符串"))
        else:
            for index, package in enumerate(value.get("packages", [])):
                root = f"packages[{index}]"
                if package.get("package_amount") is not None:
                    issues += self.collect(root + ".package_amount", [package["package_amount"]], attempt,
                        lambda row, path: self.amount_error(row, path, "package"), scalar=True)
                issues += self.collect(root + ".amount_alternatives", package.get("amount_alternatives", []), attempt,
                    lambda row, path: self.amount_error(row, path, "package"))
                issues += self.candidates(root + ".candidates", package.get("candidates", []), attempt)
            issues += self.candidates("candidates", value.get("candidates", []), attempt)
            issues += self.collect("tables", value.get("tables", []), attempt, self.table_error)
        # An omitted bad item is not a successful repair of the earlier rejection.
        represented = {issue.path for issue in issues}
        issues += [ValidationIssue(row["message"], path=path) for path, row in self.pending.items() if path not in represented]
        return issues

    def result(self, attempts):
        result = deepcopy(self.parent)
        conflicts = []
        def amount(key):
            rows = self.values.get(key, [])
            # Identical valid observations repeated in one response remain one scalar reading.
            distinct = list({json.dumps(row, sort_keys=True): row for row in rows}.values())
            if len(distinct) > 1:
                conflicts.append({"path": key, "values": distinct})
            return distinct[0] if len(distinct) == 1 else None
        if self.branch == "summary":
            result["summary_amount"] = amount("summary_amount")
            result["announcement_categories"] = self.values.get("announcement_categories", [])
        else:
            for index, package in enumerate(result["packages"]):
                root = f"packages[{index}]"
                package["package_amount"] = amount(root + ".package_amount")
                package["amount_alternatives"] = list(self.values.get(root + ".amount_alternatives", []))
                if package["package_amount"] is None:
                    package["amount_alternatives"] += self.values.get(root + ".package_amount", [])
                package["candidates"] = self.children[root + ".candidates"].outputs["candidates"]
            result["candidates"] = self.children["candidates"].outputs["candidates"]
            result["tables"] = self.values.get("tables", [])
        result["reception"] = {"attempts": deepcopy(attempts), "rejected_items": deepcopy(self.audit),
            "unrepaired": bool(self.pending or conflicts or attempts and attempts[-1].get("issues")),
            "unresolved_items": list(deepcopy(self.pending).values()), "conflicts": conflicts,
            "accepted_counts": {**{key: len(rows) for key, rows in self.values.items()},
                                **{key: len(child.outputs["candidates"]) for key, child in self.children.items()}}}
        return deepcopy(result)
