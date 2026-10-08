"""Step 8. Merge candidates inside one project.

Whether two rows are the same object is a content judgement, so the model makes it: it sees every
candidate of the package together with the evidence stamped on it, and proposes deltas against a
deterministic baseline grouping. Rules keep everything else - what a delta is allowed to say, the
arithmetic of a sum, which price bundle a row ends up with, and the reconciliation against the
package amount. With no model available the baseline stands on its own, so the step still runs and
the same input still gives the same output.

Two things are never delegated. A number is only ever written by rules: the model names rows and
the sum is recomputed here. And a source that cannot own a price - a tender requirement, or a
quote from a supplier the announcement does not name as the winner - cannot supply one, whatever
the model says.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from ict.catalog import Catalog
from ict.config import COB_FIELDS, MERGE_THINKING, SUB_FIELDS
from ict.concurrency import parallel_map
from ict.llm import LLMClient, LLMError, complete_json
from ict.schemas import (AnnouncementUnderstanding, Candidate, Cob, Failure, MergeEvidence,
                         MergedProjects, Project, Sub)

PRICE_BUNDLE = ("unit_price", "quantity", "unit", "total_price")
LINE_FIELDS = ("brand", "spec_model", "quantity", "unit_price", "total_price")
# One cent. Amounts are copied verbatim and derived totals are rounded, so anything below this is
# arithmetic noise rather than a disagreement. It replaces two business tolerances (5% for a row,
# 8% for a package) that had no basis in the data.
CENT = 0.01
MEMBER_RE = re.compile(r"\([^()]*成员[^()]*\)")
DETAIL_ROLES = {"cob_detail", "winner"}
EXCLUDE_KINDS = ("subtotal_row", "project_row", "index_row", "not_an_object")
DELTA_ORDER = ("exclude", "split", "merge", "sum", "name_from", "price_from", "fields_from",
               "category_from_announcement")
PROMPT = "merge-objects-v3"
# What fields_from is allowed to move onto a group. The two money numbers are deliberately not
# here: a unit price from one row joined to a quantity from another invents a total nobody wrote.
# They can still arrive through price_from with an explicit key list, where the source is named.
DONOR_FIELDS = ("category_name", "category_code", "category_type", "brand", "product_supplier",
                "spec_model", "quantity", "unit")
# Marks the rules used to act on. Now they are only ever written down and shown to the model.
SUSPECT_POINTER = "suspect_html_pointer_row"
SUSPECT_PROJECT = "suspect_project_name_row"


# --------------------------------------------------------------------------- values

_SHARED_CATALOG: Catalog | None = None


def _catalog() -> Catalog:
    global _SHARED_CATALOG
    if _SHARED_CATALOG is None:
        _SHARED_CATALOG = Catalog()
    return _SHARED_CATALOG


def _pack(text: str) -> str:
    return re.sub(r"\s+", "", text)


def category_items(understanding: AnnouncementUnderstanding) -> list[str]:
    """公告概要抄下来的品目条目，保持原文顺序去重。

    375 则公告的品目只写在公告概要里，正文表格与附件都没有这一列，所以第 1 步抄下来的这份
    清单是唯一来源。这里只搬运，不解释内容。
    """
    out: list[str] = []
    seen: set[str] = set()
    for item in understanding.announcement_categories or []:
        text = str(item or "").strip()
        key = _pack(text)
        if not text or key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def _category_missing(group: Group) -> bool:
    return bool(group.members) and not (_value(group.members[0], "category_name")
                                        or _value(group.members[0], "category_code"))


def _fill_category(base: Candidate, hit: dict, item: str) -> list[str]:
    """把某条公告概要品目查到的编码与名称写进这组还空着的品目格。

    值与第 7 步同一套：模型只判"这条品目说的是这个标的"，编码和名称都来自 2022 品目目录，
    规则不猜、不编。只填空格，已经写过的格不动。
    """
    filled: list[str] = []
    for key, value in (("category_code", hit.get("matched_code")),
                       ("category_name", hit.get("matched_name")),
                       ("category_type", hit.get("category_type"))):
        if value in (None, "") or _value(base, key) not in (None, ""):
            continue
        observation = base.fields[key]
        observation.normalized_value = value
        observation.status = "present"
        observation.normalization = {"filled_from": "announcement_summary", "raw_item": item,
                                     "match_type": hit.get("match_type")}
        filled.append(key)
    return filled


def _value(candidate: Candidate, key: str):
    field = candidate.fields[key]
    if field.normalized_value is not None:
        return field.normalized_value
    if field.status == "present":
        return field.raw_value
    return None


def _norm_name(value) -> str:
    text = "" if value is None else str(value)
    return re.sub(r"\s+", "", text).replace("（", "(").replace("）", ")")


def _number(value):
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        return float(str(value).replace(",", "").replace("，", ""))
    except ValueError:
        return None


def _same(left, right) -> bool:
    if left == right:
        return True
    left_number, right_number = _number(left), _number(right)
    if left_number is None or right_number is None:
        return False
    return abs(left_number - right_number) <= 0.01


def _tender(candidate: Candidate) -> bool:
    return candidate.source_class == "tender_requirement"


def _completeness(candidate: Candidate) -> int:
    keys = COB_FIELDS if candidate.entity_type == "cob" else SUB_FIELDS
    return sum(1 for key in keys if _value(candidate, key) not in (None, ""))


def _derived_total(candidate: Candidate) -> bool:
    normalization = candidate.fields["total_price"].normalization or {}
    return bool(normalization.get("filled_from"))


def _has_price(candidate: Candidate) -> bool:
    return any(_value(candidate, key) not in (None, "") for key in ("unit_price", "quantity", "total_price"))


def _pointer_row(candidate: Candidate) -> bool:
    """An HTML row that says the object exists and points every detail into the attachment.

    Both signals are statuses the extraction side already assigned: `points_to_attachment` on a
    line field, or the `line_fields_point_to_attachment` issue step 2B stamps on a whole table.
    This rule reads statuses; it does not read wording.
    """
    if candidate.source.source_type != "html" or _priced(candidate):
        return False
    if "line_fields_point_to_attachment" in candidate.issues:
        return True
    return any(candidate.fields[key].status == "points_to_attachment" for key in LINE_FIELDS)


def _bundle_of(candidate: Candidate) -> dict:
    return {key: _value(candidate, key) for key in PRICE_BUNDLE}


def _bundle_key(bundle: dict) -> tuple:
    return tuple(bundle.get("unit") if key == "unit" else _number(bundle.get(key)) for key in PRICE_BUNDLE)


def _compatible(left: dict, right: dict) -> bool:
    for key in PRICE_BUNDLE:
        a, b = left.get(key), right.get(key)
        if a in (None, "") or b in (None, ""):
            continue
        if not _same(a, b):
            return False
    return True


def _priced(candidate: Candidate) -> bool:
    """A row that can serve as a price reading: it states a unit price or a total, as a number.

    Step 7 leaves a cell that failed to parse as present with a null normalized_value, so a
    template placeholder such as '{=响应报价/数量} 元' used to arrive here as a price and was even
    offered to the repair model as an option. A row carrying only a quantity is not a reading
    either: it has nothing to say about money.
    """
    return any(_number(_value(candidate, key)) is not None for key in ("unit_price", "total_price"))


def _copy_field(base: Candidate, key: str, value) -> None:
    base.fields[key].normalized_value = value
    base.fields[key].status = "present"


def _printable(bundle: dict) -> dict:
    return {key: bundle.get(key) for key in PRICE_BUNDLE}


def is_project_name(name, project_name) -> bool:
    """A row named after the whole project is not an object; the field's own definition says so.

    Containment runs one way: the object name carries the project name, with at most a
    parenthetical or punctuation tail such as （第一期）. The old rule also accepted an object name
    that was a fragment of the project name once it covered 80% of the length. That ratio had no
    basis in the data and a short but genuine object name lost out to it, so it is gone; the model
    can still mark such a row as project_row, and that decision is logged.
    """
    item, project = _norm_name(name), _norm_name(project_name)
    if not item or not project:
        return False
    if item == project:
        return True
    if project not in item:
        return False
    return _tail_is_decoration(item.replace(project, "", 1))


def _tail_is_decoration(rest: str) -> bool:
    text = rest.strip()
    if not text:
        return True
    if text[:1] in "(（[" and text[-1:] in ")）]":
        return True
    return not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", text)


# --------------------------------------------------------------------------- context

@dataclass
class _Context:
    package_no: str
    project_name: str | None
    winners: set[str]
    detail_ids: set[str]


@dataclass
class Reading:
    """One way of reading a group's price. The model never writes these numbers."""

    label: str
    bundle: dict
    derived: bool
    sources: list[str]
    kind: str                      # single | sum


@dataclass
class Group:
    group_id: int
    members: list[Candidate] = field(default_factory=list)
    name_from: str | None = None
    price_from: str | None = None
    price_keys: list[str] | None = None
    sum_members: list[str] | None = None
    # (candidate_id, keys) pairs the model or an excluded sibling offered for blank-filling.
    donors: list[tuple[str, list[str]]] = field(default_factory=list)
    # The audit rows for this group, kept by reference so the applying stage can say whether a
    # delta actually changed anything. accepted means "legal"; applied means "it landed".
    price_entry: dict | None = None
    name_entry: dict | None = None
    donor_entries: list[dict] = field(default_factory=list)
    # 公告概要品目判给这一组的结果：原文条目、目录查到的编码/名称，以及这条 delta 的审计行。
    category_item: str | None = None
    category_hit: dict | None = None
    category_entry: dict | None = None


@dataclass
class _Cluster:
    base: Candidate
    members: list[Candidate]
    options: list[Candidate]
    chosen: Candidate | None
    readings: list[Reading] = field(default_factory=list)
    reading: Reading | None = None
    bundle: dict = field(default_factory=dict)
    derived: bool = False
    # reading | sum | partial_keys | donor_fields | no_reading_fields_only | none
    bundle_kind: str = "none"
    name_override: str | None = None


def _detail_ids(candidates: list[Candidate], evidence: MergeEvidence | None) -> set[str]:
    """HTML rows that came from a real object table, not from a summary or a score table.

    This used to be `source_priority >= 70`, a threshold with no basis in the data. The table's own
    role is a structural fact and step 2A already decided it, so it is read instead. Rows whose
    table is unknown - older runs, or a caller without evidence - are taken at face value.
    """
    tables = (evidence.html_tables if evidence is not None else {}) or {}
    ids: set[str] = set()
    for candidate in candidates:
        if candidate.entity_type != "cob" or candidate.source.source_type != "html":
            continue
        index = candidate.evidence.table_index if candidate.evidence is not None else None
        meta = tables.get(str(index)) if index is not None else None
        if meta is None or meta.get("table_role") in DETAIL_ROLES:
            ids.add(candidate.candidate_id)
    return ids


def _winner_names(items: list[Candidate], evidence: MergeEvidence | None, package_no: str) -> set[str]:
    """Everyone this package's award text names as a winner, normalized for comparison."""
    names: set[str] = set()
    for candidate in items:
        if candidate.entity_type != "sub":
            continue
        winner = _value(candidate, "is_winner")
        if winner in (None, "") or str(winner).lower() in {"false", "否", "0"}:
            continue
        name = _norm_name(_value(candidate, "supplier_name"))
        if name:
            names.add(name)
    if evidence is None:
        return names
    for name in (evidence.winner_hints or {}).get(package_no, []) or []:
        if _norm_name(name):
            names.add(_norm_name(name))
    # A document the gate read as an award notice names the winner by definition, which is how a
    # quote file earns the right to supply prices when the HTML marked nobody.
    for file_id, notice in (evidence.file_award_notices or {}).items():
        owner = (evidence.file_quote_suppliers or {}).get(file_id)
        if notice and owner and _norm_name(owner):
            names.add(_norm_name(owner))
    return names


def _rank(candidate: Candidate, ctx: _Context) -> tuple:
    """The one total order every tie in this step is broken by, so output never depends on input
    order: HTML detail first, then source priority, then how much the row actually says, then
    file, page, table and candidate id."""
    evidence = candidate.evidence
    detail = candidate.source.source_type == "html" and candidate.candidate_id in ctx.detail_ids \
        and "summary_row" not in candidate.issues \
        and candidate.fields["object_name"].status != "points_to_attachment"
    return (-int(detail), -candidate.source_priority, -_completeness(candidate),
            candidate.source.file_id or "", candidate.source.page_no or 0,
            evidence.table_index if evidence is not None and evidence.table_index is not None else 0,
            candidate.candidate_id)


def _owner_of(candidate: Candidate) -> str | None:
    evidence = candidate.evidence
    if evidence is None:
        return None
    return evidence.bidder_supplier or evidence.quote_supplier or None


def _is_winner_quote(candidate: Candidate, ctx: _Context) -> bool | None:
    if candidate.source.source_type == "html":
        return None
    owner = _owner_of(candidate)
    if not owner or not ctx.winners:
        return None
    return _norm_name(owner) in ctx.winners


def _ownership(candidate: Candidate, ctx: _Context) -> tuple[bool, bool, str | None]:
    """(may supply a price, may be an object, blocking reason).

    Two structural facts decide this, not the wording of a file: the class the screen gate gave the
    document, and whether the quote belongs to a supplier the announcement names as the winner.
    This is what replaced the closed list. The closed list barred every attachment row whose name
    did not match an HTML row character for character, which threw away whole price lists over an
    OCR'd dash - and it also happened to block a losing bidder's quote, which is the one thing
    that genuinely has to be blocked. An owner nobody can name is not blocked: the package amount
    arbitrates it instead.
    """
    if candidate.source.source_type == "html":
        return True, True, None
    if _tender(candidate):
        return False, False, "tender_requirement"
    owner = _owner_of(candidate)
    if not owner or not ctx.winners:
        return True, True, None
    if _norm_name(owner) in ctx.winners:
        return True, True, None
    return False, False, "non_winner_quote"


# --------------------------------------------------------------------------- baseline and deltas

def _baseline(cobs: list[Candidate], ctx: _Context) -> tuple[list[Group], list[Candidate]]:
    """The deterministic grouping: rows whose normalized names are identical.

    This is the floor the model edits and the whole result when there is no model. Name equality
    is not identity - it misses an OCR'd dash and it merges three different objects that share a
    name - which is exactly why the model gets to split and merge on top of it.
    """
    ordered = sorted(cobs, key=lambda item: _rank(item, ctx))
    by_name: dict[str, list[Candidate]] = {}
    nameless: list[Candidate] = []
    for candidate in ordered:
        name = _norm_name(_value(candidate, "object_name"))
        if not name:
            nameless.append(candidate)
            continue
        by_name.setdefault(name, []).append(candidate)
    buckets = sorted(by_name.values(), key=lambda members: _rank(members[0], ctx))
    return [Group(group_id=index, members=list(members)) for index, members in enumerate(buckets)], nameless


def _needs_model(cobs: list[Candidate]) -> bool:
    """One HTML row with a name has nothing to disambiguate. Anything else does.

    A row the rules only marked (pointer row, project-name row) always asks: since the rules no
    longer delete it, the model has to be the one that decides whether it stands.
    """
    if not cobs:
        return False
    if any(mark in (SUSPECT_POINTER, SUSPECT_PROJECT) for item in cobs for mark in item.warnings):
        return True
    return not (len(cobs) == 1 and cobs[0].source.source_type == "html")


def _gid(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _delta_group(delta: dict):
    """The group a delta points at. Models drop the _id suffix often enough to absorb it."""
    value = delta.get("group_id")
    return value if value is not None else delta.get("group")


def _candidate_view(candidate: Candidate, group_id: int, ctx: _Context,
                    evidence: MergeEvidence | None) -> dict:
    """One row of the table the merge model reads. Only what was actually observed is sent."""
    ev = candidate.evidence
    html = candidate.source.source_type == "html"
    view: dict = {"candidate_id": candidate.candidate_id, "group_id": group_id,
                  "origin": "html" if html else "attachment"}
    if ev is not None and ev.table_index is not None:
        view["table_index"] = ev.table_index
    if html:
        meta = ((evidence.html_tables or {}).get(str(ev.table_index))
                if evidence is not None and ev is not None and ev.table_index is not None else None) or {}
        if meta.get("table_role"):
            view["table_role"] = meta["table_role"]
        if meta.get("section"):
            view["table_section"] = meta["section"]
    else:
        view["file_id"] = candidate.source.file_id
        names = (evidence.file_names if evidence is not None else {}) or {}
        view["file_name"] = names.get(candidate.source.file_id or "")
        classes = (evidence.file_classes if evidence is not None else {}) or {}
        view["file_class"] = candidate.source_class or classes.get(candidate.source.file_id or "")
        if candidate.source.page_no is not None:
            view["page_no"] = candidate.source.page_no
        owner = _owner_of(candidate)
        if owner:
            view["quote_supplier"] = owner
        winner_quote = _is_winner_quote(candidate, ctx)
        if winner_quote is not None:
            view["is_winner_quote"] = winner_quote
    if ev is not None:
        if ev.row_text:
            view["row_text"] = ev.row_text
        if ev.bidder_supplier:
            view["bidder_supplier"] = ev.bidder_supplier
        if ev.winner_supplier:
            view["winner_supplier"] = ev.winner_supplier
    for key in COB_FIELDS:
        value = _value(candidate, key)
        if value not in (None, ""):
            view[key] = value
    view["field_states"] = {key: candidate.fields[key].status for key in COB_FIELDS}
    if candidate.issues:
        view["issues"] = list(candidate.issues)
    # Facts the rules can already state, and used to act on silently. The model gets them so it
    # stops offering prices it cannot have and stops trusting a template cell that reads
    # {=响应报价/数量}元.
    may_price, may_own, why = _ownership(candidate, ctx)
    view["price_reading"] = bool(_priced(candidate) and may_price)
    if not may_price:
        view["price_ineligible"] = why or "source_cannot_own_price"
    if not may_own:
        view["owner_suspect"] = why or "cannot_own_object"
    unparsable = [key for key in PRICE_BUNDLE
                  if candidate.fields[key].status in ("unparsable", "low_confidence")]
    if unparsable:
        view["unparsable_fields"] = unparsable
    marks = [item for item in candidate.warnings if item in (SUSPECT_POINTER, SUSPECT_PROJECT)]
    if marks:
        view["rule_suspect"] = marks
    return {key: value for key, value in view.items() if value not in (None, "", [], {})}


def _ask_merge(ctx: _Context, groups: list[Group], cobs: list[Candidate], amount: float | None,
               raw: str | None, suspect: str | None, evidence: MergeEvidence | None,
               llm: LLMClient, counter,
               categories: list[str] | None = None) -> tuple[list[dict], str | None, str | None]:
    by_group = {candidate.candidate_id: group.group_id for group in groups for candidate in group.members}
    payload = {
        "project_name": ctx.project_name,
        "package_no": ctx.package_no,
        "package_total_amount": amount,
        "package_amount_raw": raw,
        "package_amount_suspect": suspect is not None,
        "winner_suppliers": sorted(ctx.winners),
        "baseline_groups": [{"group_id": group.group_id,
                             "candidate_ids": [item.candidate_id for item in group.members]}
                            for group in groups],
        "candidates": [_candidate_view(item, by_group.get(item.candidate_id, -1), ctx, evidence)
                       for item in sorted(cobs, key=lambda entry: _rank(entry, ctx))],
    }
    if categories:
        # 公告概要抄下来的品目，原文给到模型；选哪条属于内容判断，编码与名称由规则查目录补。
        payload["announcement_categories"] = list(categories)
    group_ids = {group.group_id for group in groups}
    candidate_ids = {item.candidate_id for item in cobs}
    category_keys = {_pack(item) for item in (categories or [])}
    notes: dict = {}
    try:
        produced = complete_json(llm, step="merge_candidates", prompt_version=PROMPT,
                                 user=json.dumps(payload, ensure_ascii=False),
                                 validate=lambda parsed: _validate_merge(parsed, group_ids, candidate_ids,
                                                                         category_keys),
                                 counter=counter, thinking=MERGE_THINKING, notes=notes)
    except (LLMError, ValueError) as exc:
        return [], ("llm_call_failed" if isinstance(exc, LLMError) else "llm_schema_invalid"), None
    deltas = produced.get("deltas")
    return (deltas if isinstance(deltas, list) else []), None, (notes.get("reasoning") or None)


def _validate_merge(parsed: dict, group_ids: set[int], candidate_ids: set[str],
                    category_keys: set[str] | None = None) -> str:
    deltas = parsed.get("deltas")
    if not isinstance(deltas, list):
        return "需要 deltas 数组"
    for delta in deltas:
        if not isinstance(delta, dict):
            return "deltas 每一项必须是对象"
        op = delta.get("op")
        if op not in DELTA_ORDER:
            return "op 不在允许值内"
        gid = _gid(_delta_group(delta))
        if op == "exclude":
            if delta.get("candidate_id") not in candidate_ids:
                return "exclude 的 candidate_id 不在输入里"
            if delta.get("kind") not in EXCLUDE_KINDS:
                return "exclude 的 kind 只能取 subtotal_row、project_row、index_row、not_an_object"
        elif op == "merge":
            wanted = [_gid(item) for item in (delta.get("groups") or [])]
            if len(wanted) < 2 or any(item is None or item not in group_ids for item in wanted):
                return "merge 需要至少两个已知 group_id"
        elif op == "split":
            if gid not in group_ids:
                return "split 的 group_id 不在输入里"
            parts = delta.get("parts")
            if not isinstance(parts, list) or len(parts) < 2:
                return "split 需要至少两个 parts"
        elif op == "sum":
            if gid not in group_ids:
                return "sum 的 group_id 不在输入里"
            members = delta.get("members")
            if not isinstance(members, list) or len(members) < 2:
                return "sum 需要至少两个 members"
            if any(item not in candidate_ids for item in members):
                return "sum 的 members 里有未知 candidate_id"
        elif op == "fields_from":
            if gid not in group_ids:
                return "fields_from 的 group_id 不在输入里"
            if delta.get("candidate_id") not in candidate_ids:
                return "fields_from 的 candidate_id 不在输入里"
            keys = delta.get("keys")
            if not isinstance(keys, list) or not keys:
                return "fields_from 需要 keys 列表"
            if any(item not in DONOR_FIELDS for item in keys):
                return "fields_from 的 keys 只能取：" + "、".join(DONOR_FIELDS)
        elif op == "price_from":
            if gid not in group_ids:
                return "price_from 的 group_id 不在输入里"
            if delta.get("candidate_id") not in candidate_ids:
                return "price_from 的 candidate_id 不在输入里"
            keys = delta.get("keys")
            if keys is not None and (not isinstance(keys, list) or not keys
                                     or any(item not in PRICE_BUNDLE for item in keys)):
                return "price_from 的 keys 只能取 unit_price、quantity、unit、total_price 的子集"
        elif op == "category_from_announcement":
            if gid not in group_ids:
                return "category_from_announcement 的 group_id 不在输入里"
            item = delta.get("raw_item")
            if not isinstance(item, str) or _pack(item) not in (category_keys or set()):
                return "category_from_announcement 的 raw_item 必须是公告概要里给出的品目原文之一"
        else:
            if gid not in group_ids:
                return "%s 的 group_id 不在输入里" % op
            if delta.get("candidate_id") not in candidate_ids:
                return "%s 的 candidate_id 不在输入里" % op
    return ""


def _apply_deltas(groups: list[Group], deltas: list[dict], by_id: dict[str, Candidate],
                  ctx: _Context, decisions: list[dict],
                  categories: dict[str, str] | None = None
                  ) -> tuple[list[Group], list[tuple[Candidate, str]]]:
    """Apply the model's edits one at a time. A delta that does not hold is dropped on its own.

    Nothing is all-or-nothing: an unresolvable group id costs that one edit, not the package. The
    order is fixed so the result cannot depend on the order the model happened to write them in.

    Two things changed with the v2 deltas. An excluded row now donates the fields it stated to the
    group it came from, because exclusion used to mean those values disappeared - the category an
    announcement listed once was lost with the row that carried it. And every accepted delta keeps
    its own audit row by reference, so the stage that writes values can say whether the edit landed:
    `accepted` means it was legal, `applied` means it changed the output.
    """
    live: dict[int, Group] = {group.group_id: group for group in groups}
    alias: dict[int, int] = {group.group_id: group.group_id for group in groups}
    next_id = max(live) + 1 if live else 0
    excluded: list[tuple[Candidate, str]] = []
    dropped: set[str] = set()

    def resolve(value) -> Group | None:
        gid = _gid(value)
        if gid is None:
            return None
        return live.get(alias.get(gid, gid))

    def log(delta: dict, accepted: bool, reject: str | None = None, extra: dict | None = None) -> dict:
        entry = {"package_no": ctx.package_no, "accepted": accepted}
        entry.update({key: value for key, value in delta.items() if key in
                      ("op", "group_id", "group", "groups", "parts", "members", "candidate_id", "kind",
                       "keys", "raw_item", "name_from", "price_from", "reason", "confidence")})
        if reject:
            entry["reject"] = reject
        if extra:
            entry.update(extra)
        decisions.append(entry)
        return entry

    ordered = sorted((delta for delta in deltas if isinstance(delta, dict)),
                     key=lambda delta: DELTA_ORDER.index(delta.get("op"))
                     if delta.get("op") in DELTA_ORDER else len(DELTA_ORDER))
    for delta in ordered:
        op = delta.get("op")
        if op == "exclude":
            candidate = by_id.get(delta.get("candidate_id"))
            if candidate is None or candidate.candidate_id in dropped:
                log(delta, False, "unknown_candidate")
                continue
            group = next((item for item in live.values() if candidate in item.members), None)
            donated: list[str] = []
            if group is not None and len(group.members) == 1:
                live.pop(group.group_id, None)
            elif group is not None:
                group.members.remove(candidate)
                donated = [key for key in DONOR_FIELDS if _value(candidate, key) not in (None, "")]
                if donated:
                    group.donors.append((candidate.candidate_id, donated))
            dropped.add(candidate.candidate_id)
            excluded.append((candidate, str(delta.get("kind"))))
            log(delta, True, extra={"donated_keys": donated} if donated else None)
        elif op == "split":
            group = resolve(_delta_group(delta))
            if group is None:
                log(delta, False, "unknown_group")
                continue
            parts, seen = [], set()
            for part in delta.get("parts") or []:
                members = [by_id[item] for item in (part or []) if item in by_id and item not in seen]
                seen.update(item.candidate_id for item in members)
                if members:
                    parts.append(members)
            if len(parts) < 2 or seen != {item.candidate_id for item in group.members}:
                log(delta, False, "parts_do_not_partition_group")
                continue
            live.pop(group.group_id, None)
            created = []
            for members in parts:
                live[next_id] = Group(group_id=next_id, members=members)
                created.append(next_id)
                next_id += 1
            alias[group.group_id] = created[0]
            log(delta, True, extra={"new_group_ids": created})
        elif op == "merge":
            sources = []
            for value in delta.get("groups") or []:
                group = resolve(value)
                if group is not None and group not in sources:
                    sources.append(group)
            if len(sources) < 2:
                log(delta, False, "fewer_than_two_live_groups")
                continue
            members = [item for group in sources for item in group.members]
            for group in sources:
                live.pop(group.group_id, None)
            live[next_id] = Group(group_id=next_id, members=members)
            for group in sources:
                alias[group.group_id] = next_id
            log(delta, True, extra={"new_group_id": next_id})
            next_id += 1
        elif op == "sum":
            group = resolve(_delta_group(delta))
            if group is None:
                log(delta, False, "unknown_group")
                continue
            owned = {item.candidate_id for item in group.members}
            members = [item for item in (delta.get("members") or []) if item in owned]
            if len(members) < 2:
                log(delta, False, "members_not_in_group")
                continue
            group.sum_members = members
            log(delta, True)
        elif op == "fields_from":
            group = resolve(_delta_group(delta))
            target = by_id.get(delta.get("candidate_id"))
            keys = [item for item in (delta.get("keys") or []) if item in DONOR_FIELDS]
            if group is None or target is None:
                log(delta, False, "unknown_group_or_candidate")
                continue
            if not keys:
                log(delta, False, "no_allowed_keys")
                continue
            entry = log(delta, True, extra={"keys": keys})
            group.donors.append((target.candidate_id, keys))
            group.donor_entries.append(entry)
        elif op == "price_from":
            group = resolve(_delta_group(delta))
            target = delta.get("candidate_id")
            if group is None or target not in {item.candidate_id for item in group.members}:
                log(delta, False, "candidate_not_in_group")
                continue
            if not _ownership(by_id[target], ctx)[0]:
                log(delta, False, "source_cannot_own_price")
                continue
            row = by_id[target]
            keys = [item for item in (delta.get("keys") or []) if item in PRICE_BUNDLE] or None
            if keys is None and not _priced(row):
                # Asking for the whole bundle from a row that states no number is how an accepted
                # delta used to mean nothing at all. It is refused here now, and said here.
                log(delta, False, "target_states_no_number_price")
                continue
            if keys and all(_value(row, key) in (None, "") for key in keys):
                log(delta, False, "target_states_none_of_the_requested_keys")
                continue
            entry = log(delta, True, extra={"keys": keys} if keys else None)
            group.price_from = target
            group.price_keys = keys
            group.price_entry = entry
        elif op == "category_from_announcement":
            group = resolve(_delta_group(delta))
            if group is None:
                log(delta, False, "unknown_group")
                continue
            canonical = (categories or {}).get(_pack(str(delta.get("raw_item") or "")))
            if not canonical:
                log(delta, False, "item_not_in_announcement")
                continue
            hit = _catalog().match(None, canonical)
            if hit.get("match_type") == "unmatched":
                # 目录里查不到就不写：宁可这组品目为空，也不把模型的话当成值。
                log(delta, False, "category_unmatched")
                continue
            entry = log(delta, True, extra={"raw_item": canonical,
                                            "match_type": hit.get("match_type"),
                                            "matched_code": hit.get("matched_code"),
                                            "matched_name": hit.get("matched_name")})
            group.category_item = canonical
            group.category_hit = hit
            group.category_entry = entry
        elif op == "name_from":
            group = resolve(_delta_group(delta))
            target = delta.get("candidate_id")
            if group is None or target not in {item.candidate_id for item in group.members}:
                log(delta, False, "candidate_not_in_group")
                continue
            entry = log(delta, True)
            group.name_from = target
            group.name_entry = entry
    return sorted(live.values(), key=lambda group: _rank(group.members[0], ctx)), excluded


def _donors_of(group: Group, by_id: dict[str, Candidate]) -> list[tuple[Candidate, list[str]]]:
    """Rows the model or an excluded sibling offered as blank-fill donors for this group."""
    out: list[tuple[Candidate, list[str]]] = []
    for donor_id, keys in group.donors:
        donor = by_id.get(donor_id)
        if donor is not None and keys:
            out.append((donor, keys))
    return out


def _donate_excluded(excluded: list[tuple[Candidate, str]], groups: list[Group], ctx: _Context,
                     decisions: list[dict]) -> None:
    """When the package is left with one object, an excluded row still describes it.

    The old code moved only a project-named row's price across and dropped its category with the
    row. Only the unambiguous case is donated: exactly one group left. With several objects a
    summary row's brand cannot be assigned to one of them, and a guess is not an observation - the
    model can still move those fields itself with fields_from, one named row at a time.
    """
    live = [group for group in groups if group.members]
    for candidate, kind in excluded:
        keys = [key for key in DONOR_FIELDS if _value(candidate, key) not in (None, "")]
        if kind == "project_row":
            keys += [key for key in PRICE_BUNDLE if _value(candidate, key) not in (None, "")]
        if len(live) != 1 or not keys:
            continue
        if not _ownership(candidate, ctx)[0]:
            keys = [key for key in keys if key not in ("unit_price", "total_price")]
        if not keys:
            continue
        live[0].donors.append((candidate.candidate_id, keys))
        decisions.append({"package_no": ctx.package_no, "op": "donate", "accepted": True,
                          "candidate_id": candidate.candidate_id, "keys": keys,
                          "group_id": live[0].group_id, "kind": kind})


# --------------------------------------------------------------------------- arithmetic

def _sum_bundle(members: list[Candidate]) -> tuple[dict | None, str | None]:
    """Recompute a summed bundle. The model names the rows; the number is ours.

    Refused unless the rows can actually be added: every one of them has a quantity, they agree on
    the unit (or all stay silent about it), and they do not disagree on the unit price - a split
    row keeps its price by definition, so differing prices mean these are different readings, not
    parts of one object.
    """
    if len(members) < 2:
        return None, "sum_too_few_members"
    quantities, totals, prices, units = [], [], [], set()
    for candidate in members:
        quantity = _number(_value(candidate, "quantity"))
        if quantity is None:
            return None, "sum_missing_quantity"
        quantities.append(quantity)
        total = _number(_value(candidate, "total_price"))
        if total is not None:
            totals.append(total)
        price = _number(_value(candidate, "unit_price"))
        if price is not None:
            prices.append(price)
        unit = _value(candidate, "unit")
        if unit not in (None, ""):
            units.add(str(unit).strip())
    if len(units) > 1:
        return None, "sum_unit_conflict"
    if len({round(price, 4) for price in prices}) > 1:
        return None, "sum_price_conflict"
    unit_price = prices[0] if prices else None
    quantity = round(sum(quantities), 6)
    if len(totals) == len(members):
        total = round(sum(totals), 4)
    elif unit_price is not None:
        total = round(unit_price * quantity, 4)
    else:
        return None, "sum_no_value"
    return {"unit_price": unit_price, "quantity": quantity,
            "unit": next(iter(units)) if units else None, "total_price": total}, None


def _fill_bundle(chosen: Candidate, options: list[Candidate]) -> tuple[dict, bool]:
    """Blank fields of the chosen bundle, filled only from bundles that do not contradict it."""
    bundle = _bundle_of(chosen)
    derived = _derived_total(chosen)
    for other in options:
        if other is chosen:
            continue
        values = _bundle_of(other)
        if not _compatible(bundle, values):
            continue
        for key in PRICE_BUNDLE:
            if bundle[key] in (None, "") and values[key] not in (None, ""):
                bundle[key] = values[key]
                if key == "total_price":
                    derived = _derived_total(other)
    return bundle, derived


def _readings(group: Group, ctx: _Context, by_id: dict[str, Candidate],
              decisions: list[dict]) -> tuple[list[Reading], list[Candidate]]:
    """Every price this group could end up with, most authoritative first."""
    eligible = [item for item in sorted(group.members, key=lambda entry: _rank(entry, ctx))
                if _ownership(item, ctx)[0]]
    # A reading has to state a price, but a blank may still be filled from any row that says
    # something about money: a row carrying only a quantity is not a reading, yet dropping it as a
    # donor loses a quantity nobody else wrote.
    donors = [item for item in eligible if _has_price(item)]
    options = [item for item in eligible if _priced(item)]
    readings: list[Reading] = []
    seen: set[tuple] = set()
    for candidate in options:
        bundle, derived = _fill_bundle(candidate, donors)
        key = _bundle_key(bundle)
        if key in seen:
            continue
        seen.add(key)
        readings.append(Reading(label=candidate.candidate_id, bundle=bundle, derived=derived,
                                sources=[candidate.candidate_id], kind="single"))
    if group.sum_members:
        members = [by_id[item] for item in group.sum_members if item in by_id]
        bundle, reason = _sum_bundle(members)
        if bundle is None:
            decisions.append({"package_no": ctx.package_no, "op": "sum", "group_id": group.group_id,
                              "members": group.sum_members, "accepted": False, "reject": reason})
        elif _bundle_key(bundle) not in seen:
            readings.append(Reading(label="sum:%d" % group.group_id, bundle=bundle, derived=True,
                                    sources=list(group.sum_members), kind="sum"))
    return readings, options


def _reconcile(group: Group, ctx: _Context, by_id: dict[str, Candidate], conflicts: list[dict],
               decisions: list[dict]) -> _Cluster:
    """One group into one row.

    Description fields are filled by rank, so an HTML detail row still wins over an attachment row
    that says less, and an attachment row now fills what the HTML left empty - the manufacturer
    included. Named donors (fields_from, and the fields of a row the model excluded) arrive through
    the same blank-fill door: a value moves only when the row does not already state that field, and
    two live statements that disagree become a logged conflict instead of a silent overwrite.

    Money is the exception, and its failure path is what changed. The four price fields still come
    from one reading, because a unit price from one row joined to a quantity from another invents a
    total nobody wrote. But a group with no usable reading used to lose all four, including a
    quantity and a unit every row in it stated - that is how 1.0 项 and 套 disappeared from whole
    packages. Those two observations now stay on the row, audited as `no_reading_fields_only`.
    """
    members = sorted(group.members, key=lambda item: _rank(item, ctx))
    base = members[0]
    for extra in members[1:]:
        _fill_onto(base, extra, list(COB_FIELDS), conflicts, ctx)
    price_fill: dict = {}
    for donor, keys in _donors_of(group, by_id):
        filled: list[str] = []
        may_price = _ownership(donor, ctx)[0]
        for key in keys:
            value = _value(donor, key)
            if value in (None, ""):
                continue
            if key in PRICE_BUNDLE:
                if key in ("unit_price", "total_price") and (not may_price or _number(value) is None):
                    continue
                if _value(base, key) in (None, "") and price_fill.get(key) in (None, ""):
                    price_fill[key] = value
                    filled.append(key)
                continue
            before = _value(base, key)
            _fill_onto(base, donor, [key], conflicts, ctx)
            if before in (None, "") and _value(base, key) not in (None, ""):
                filled.append(key)
        for entry in group.donor_entries:
            if entry.get("candidate_id") == donor.candidate_id:
                entry["applied"] = bool(filled)
                entry["filled"] = sorted(set(filled))
    if group.category_hit is not None:
        # 品目只写在公告概要里的公告：模型在这一步判"哪条品目说的是这个标的"，值由目录给出。
        # 放在命名捐赠之后，文档里真写过的品目优先，这里只补还空着的格。
        filled = _fill_category(base, group.category_hit, group.category_item or "")
        if group.category_entry is not None:
            group.category_entry["applied"] = bool(filled)
            group.category_entry["filled"] = sorted(filled)
    readings, options = _readings(group, ctx, by_id, decisions)
    reading = None
    if group.price_from:
        reading = next((item for item in readings if item.label == group.price_from), None)
    used_target = reading is not None
    if reading is None and readings:
        reading = readings[0]
    bundle: dict = {}
    kind = "none"
    target_row = by_id.get(group.price_from) if group.price_from else None
    if group.price_keys and target_row is not None:
        # A named subset means those fields come from that row and from nowhere else; the rest of
        # the four stay as this row already had them. Money keys still need a real number and a
        # source allowed to supply one.
        bundle = {key: _value(base, key) for key in PRICE_BUNDLE}
        may_price = _ownership(target_row, ctx)[0]
        took = []
        for key in group.price_keys:
            value = _value(target_row, key)
            if value in (None, ""):
                continue
            if key in ("unit_price", "total_price") and (not may_price or _number(value) is None):
                continue
            bundle[key] = value
            took.append(key)
        kind = "partial_keys" if took else "none"
        used_target = bool(took)
    elif reading is not None:
        bundle, kind = dict(reading.bundle), reading.kind
    if not any(value not in (None, "") for value in bundle.values()):
        # Empty rather than missing: a keys list that took nothing leaves a dict of nulls, and that
        # must not swallow the fallback below.
        for key in ("quantity", "unit"):
            for donor in [base] + members:
                value = _value(donor, key)
                if value not in (None, ""):
                    bundle[key] = value
                    kind = "no_reading_fields_only"
                    break
    for key, value in price_fill.items():
        if bundle.get(key) in (None, ""):
            bundle[key] = value
            if kind in ("none", "no_reading_fields_only"):
                kind = "donor_fields"
    if group.price_entry is not None:
        group.price_entry["applied"] = used_target or (reading is not None and not group.price_from)
        if group.price_keys:
            group.price_entry["took"] = [key for key in group.price_keys if bundle.get(key) not in (None, "")]
        group.price_entry["bundle"] = kind
        if group.price_from and not used_target and reading is not None:
            # The named row could not be a reading, so another row's money was used instead. That is
            # a real outcome, not a silent one: say which row supplied it.
            group.price_entry["used_instead"] = reading.label
    name_override = None
    if group.name_from and group.name_from in by_id:
        name_override = _value(by_id[group.name_from], "object_name")
        if group.name_entry is not None:
            group.name_entry["applied"] = bool(name_override)
    chosen = None
    if reading is not None and reading.kind == "single":
        chosen = next((item for item in options if item.candidate_id == reading.label), None)
    return _Cluster(base=base, members=list(group.members), options=options, chosen=chosen,
                    readings=readings, reading=reading, bundle=bundle,
                    derived=bool(reading is not None and reading.derived),
                    bundle_kind=kind, name_override=name_override)


def _fill_onto(base: Candidate, extra: Candidate, keys, conflicts: list[dict],
               ctx: _Context) -> None:
    """Blank fields of `base` take `extra`'s value. Two statements that disagree are logged."""
    for key in keys:
        if key in PRICE_BUNDLE:
            continue
        left, right = _value(base, key), _value(extra, key)
        if right in (None, ""):
            continue
        if left in (None, ""):
            _copy_field(base, key, right)
            continue
        if _same(left, right) or (key == "brand" and base.candidate_id in ctx.detail_ids):
            continue
        base.fields[key].status = "conflict"
        conflicts.append({"field": key, "candidate_ids": [base.candidate_id, extra.candidate_id],
                          "values": [left, right]})


def _package_total(clusters: list[_Cluster]) -> float | None:
    totals = [_number(item.bundle.get("total_price")) for item in clusters]
    totals = [value for value in totals if value is not None]
    return sum(totals) if totals else None


def _arbitrate(clusters: list[_Cluster], amount: float, package_no: str, repairs: list[dict],
               decisions: list[dict]) -> None:
    """Give the package amount a say wherever a row has more than one reading.

    There is no dead zone. The old code consulted the amount only once the line total was more
    than 8% off, which is how a package whose amount was 1239629.63 kept a line reading of
    1241056.19: 0.1% apart, so the one independent number available was never asked. Only a
    strict improvement is taken, one reading at a time, so a row already as close as it can get is
    left alone and the result does not depend on the order clusters are visited.
    """
    for _ in range(len(clusters) + 1):
        total = _package_total(clusters)
        if total is None:
            return
        gap = abs(total - amount)
        best = None
        for cluster in clusters:
            current = _number(cluster.bundle.get("total_price"))
            if current is None or len(cluster.readings) < 2:
                continue
            for reading in cluster.readings:
                if cluster.reading is not None and reading.label == cluster.reading.label:
                    continue
                value = _number(reading.bundle.get("total_price"))
                if value is None:
                    continue
                new_gap = abs(total - current + value - amount)
                if new_gap + 1e-9 < gap and (best is None or new_gap < best[0]):
                    best = (new_gap, cluster, reading)
        if best is None:
            _log_unchosen_sums(clusters, package_no, decisions)
            return
        new_gap, cluster, reading = best
        previous = cluster.reading
        cluster.reading = reading
        cluster.bundle = dict(reading.bundle)
        cluster.derived = reading.derived
        cluster.chosen = (next((item for item in cluster.options if item.candidate_id == reading.label), None)
                          if reading.kind == "single" else None)
        repairs.append({"package_no": package_no, "action": "price_bundle_by_amount",
                        "from": None if previous is None else previous.label, "to": reading.label,
                        "gap_before": round(gap, 2), "gap_after": round(new_gap, 2)})


def _log_unchosen_sums(clusters: list[_Cluster], package_no: str, decisions: list[dict],
                       reason: str = "sum_not_chosen_by_amount") -> None:
    """A sum the model asked for and the rules did not apply. The rows stay one object either way.

    Without a package amount there is nothing to check a sum against, so the object is kept but its
    numbers stay as the most authoritative single row wrote them: a wrong-but-observed quantity
    rather than an invented one. The log says which of the two happened.
    """
    for cluster in clusters:
        summed = next((item for item in cluster.readings if item.kind == "sum"), None)
        if summed is None or (cluster.reading is not None and cluster.reading.label == summed.label):
            continue
        decisions.append({"package_no": package_no, "op": "sum", "accepted": False,
                          "reject": reason, "members": summed.sources,
                          "chosen": None if cluster.reading is None else cluster.reading.label})


def _suspect_amount(amount: float | None, raw: str | None) -> str | None:
    """The arbitrator has to be sound itself before it can arbitrate.

    Both tests are about the amount alone. A percentage is not money - `parse_amount` refuses one
    now, and this is the belt. A total that is not positive cannot be an award amount; that is how
    折扣率：96.60% once became a package total of 96.6 yuan.

    Comparing the amount against the lines was tried and dropped: one spurious object is enough to
    make a sound amount look impossible, and quarantining it removes the very signal that would
    have exposed the object. A line that does not fit is amount_overflow's job, not this one's.
    """
    if amount is None:
        return "missing"
    if raw and re.search(r"[%％]", str(raw)):
        return "percent"
    if amount <= 0:
        return "non_positive"
    return None


def _alternatives(clusters: list[_Cluster]) -> list[dict]:
    """Groups with more than one reading, handed to step 8b to choose between."""
    found = []
    for index, cluster in enumerate(clusters):
        if len(cluster.readings) < 2:
            continue
        found.append({
            "cluster_id": index,
            "cob_index": index,
            "object_name": str(_value(cluster.base, "object_name")),
            "chosen": None if cluster.reading is None else cluster.reading.label,
            "options": [
                {
                    "candidate_id": reading.label,
                    "reading_kind": reading.kind,
                    "sources": reading.sources,
                    "source_type": "html" if reading.kind == "sum" else
                    (next((item.source.source_type for item in cluster.options
                           if item.candidate_id == reading.label), None)),
                    "file_id": None if reading.kind == "sum" else
                    (next((item.source.file_id for item in cluster.options
                           if item.candidate_id == reading.label), None)),
                    "source_priority": None if reading.kind == "sum" else
                    (next((item.source_priority for item in cluster.options
                           if item.candidate_id == reading.label), None)),
                    **_printable(reading.bundle),
                    "total_is_derived": reading.derived,
                }
                for reading in cluster.readings
            ],
        })
    return found


# --------------------------------------------------------------------------- package amount

def _package_amount(package, single: bool, understanding: AnnouncementUnderstanding,
                    observed: dict[str, list], repairs: list[dict]) -> tuple[float | None, str | None, str | None]:
    """One amount per package, from a fixed priority chain. Never derived from the lines.

    The announcement wins. Only where it says nothing does the attachment's own observation get a
    turn, and only when every observer copied the same text - values that disagree are left
    unfilled and stay in the log. An attachment-sourced amount is then excluded from
    reconciliation: a file that supplies both the lines and the total would only confirm itself.
    """
    if package.package_amount and package.package_amount.amount_yuan is not None:
        return package.package_amount.amount_yuan, "announcement", package.package_amount.raw_text
    if single and understanding.summary_amount and understanding.summary_amount.amount_yuan is not None:
        return understanding.summary_amount.amount_yuan, "announcement", understanding.summary_amount.raw_text
    values = [str(item.raw_text) for item in observed.get(package.package_no, []) if item.amount_yuan is not None]
    distinct = list(dict.fromkeys(values))
    if len(distinct) == 1:
        picked = next(item for item in observed[package.package_no] if str(item.raw_text) == distinct[0])
        repairs.append({"package_no": package.package_no, "action": "package_amount_from_attachment",
                        "value": picked.amount_yuan, "raw_text": picked.raw_text, "file_id": picked.file_id,
                        "file_name": picked.file_name, "page_no": picked.page_no})
        return picked.amount_yuan, "attachment", picked.raw_text
    if len(distinct) > 1:
        repairs.append({"package_no": package.package_no, "action": "package_amount_conflict",
                        "values": distinct, "files": [item.file_id for item in observed[package.package_no]]})
        return None, "conflict", None
    return None, None, None


def _move_project_price(project_rows: list[Candidate], others: list[Candidate],
                        unmatched: list[dict], ctx: _Context) -> None:
    """A row named after the whole project is not an object; its price goes to the only real one.

    Only the highest-ranked project row supplies the price. Letting every project row fill in turn
    made the result depend on the order candidates arrived in, so a losing bidder's project-named
    row could get there first and donate a bid price instead of the award amount.
    """
    target_names = {_norm_name(_value(item, "object_name")) for item in others}
    single_target = len(target_names) == 1 and not any(_has_price(item) for item in others)
    ranked = sorted(project_rows, key=lambda item: _rank(item, ctx))
    donor = ranked[0] if single_target and _has_price(ranked[0]) else None
    for row in ranked:
        if row is not donor:
            unmatched.append({"candidate_id": row.candidate_id, "reason": "项目全称行不作为标的"})
            continue
        for target in others:
            for key in PRICE_BUNDLE:
                value = _value(row, key)
                if value not in (None, "") and _value(target, key) in (None, ""):
                    _copy_field(target, key, value)
                    target.fields[key].normalization = (
                        {"filled_from": "project_row"} if key == "total_price" else None)
        unmatched.append({"candidate_id": row.candidate_id, "reason": "项目全称行，价格并入唯一标的"})


# --------------------------------------------------------------------------- step entry

class _PackageTrace:
    """The shared audit lists, buffered per package.

    Each package writes into its own buffers; the parent flushes them in package order once every
    package is done. That keeps `merge_decisions`, `repairs`, `conflicts` and
    `unmatched_summary_rows` in exactly the order the serial loop produced them.
    """

    def __init__(self) -> None:
        self.repairs: list[dict] = []
        self.decisions: list[dict] = []
        self.conflicts: list[dict] = []
        self.unmatched: list[dict] = []


def merge_projects(run_id: str, understanding: AnnouncementUnderstanding, candidates: list[Candidate],
                   package_amounts: list | None = None, evidence: MergeEvidence | None = None,
                   llm: LLMClient | None = None, counter=None) -> MergedProjects:
    known = {package.package_no: package for package in understanding.packages}
    conflicts: list[dict] = []
    unassigned: list[dict] = []
    unmatched: list[dict] = []
    repairs: list[dict] = []
    decisions: list[dict] = []
    notes: dict[str, str] = {}
    alternatives: dict[str, list[dict]] = {}
    list_sources: dict[str, str] = {}
    amount_origins: dict[str, str] = {}
    amount_audit: dict[str, dict] = {}
    observed: dict[str, list] = {}
    for item in package_amounts or []:
        observed.setdefault(str(item.package_no), []).append(item)
    grouped: dict[str, list[Candidate]] = {package.project_id: [] for package in understanding.packages}
    for candidate in candidates:
        if candidate.project_id not in grouped:
            unassigned.append({"candidate_id": candidate.candidate_id, "reason": "无法确定包号"})
            continue
        if candidate.package_no and candidate.package_no not in known:
            candidate.validation_errors.append("package_not_found")
            unassigned.append({"candidate_id": candidate.candidate_id, "reason": "包号不在公告包列表"})
            continue
        grouped[candidate.project_id].append(candidate)

    projects: list[Project] = []
    single = understanding.package_mode == "single"
    # 公告概要里抄下来的品目（可能为空）。它只写在概要里，正文表格与附件都没有这一列的公告
    # 有 375 则；没有这一句，那些标的的品目永远填不上。
    categories = category_items(understanding)
    category_map = {_pack(item): item for item in categories}

    # Phase 1, in package order: everything a package needs before the model sees it. The local
    # decision rules write into that package's own buffers, never into the shared lists.
    prepared: list[dict] = []
    for package in understanding.packages:
        items = grouped.get(package.project_id, [])
        trace = _PackageTrace()
        amount, origin, raw = _package_amount(package, single, understanding, observed, trace.repairs)
        amount_origins[package.package_no] = origin
        ctx = _Context(package_no=package.package_no, project_name=understanding.project_name,
                       winners=_winner_names(items, evidence, package.package_no),
                       detail_ids=_detail_ids(items, evidence))

        cobs = [item for item in items if item.entity_type == "cob"]
        # This pass used to delete rows before the model ever saw them. It now marks, and the model
        # decides. Deletion stays only for the no-model run, where the deterministic baseline has to
        # stand on its own (unit tests and offline replay).
        pointers = [item for item in cobs if _pointer_row(item)]
        if pointers and any(_ownership(item, ctx)[1] and _priced(item)
                            for item in cobs if item not in pointers):
            # t20260202_26140146: the HTML says one object exists and points every field into the
            # attachment, where seven priced rows are waiting. Keeping both gives eight objects.
            for item in pointers:
                if SUSPECT_POINTER not in item.warnings:
                    item.warnings.append(SUSPECT_POINTER)
            if llm is None:
                for item in pointers:
                    trace.unmatched.append({"candidate_id": item.candidate_id,
                                            "reason": "html_pointer_row"})
                cobs = [item for item in cobs if item not in pointers]
        project_rows = [item for item in cobs if is_project_name(_value(item, "object_name"), ctx.project_name)]
        others = [item for item in cobs if item not in project_rows]
        itemized = [item for item in others
                    if item.source.source_type == "html" and _ownership(item, ctx)[1]]
        if project_rows and itemized:
            for item in project_rows:
                if SUSPECT_PROJECT not in item.warnings:
                    item.warnings.append(SUSPECT_PROJECT)
            if llm is None:
                # Dropped only when the announcement itself itemizes something else. A single-object
                # service contract is very often named after its own object, and treating that name
                # as a summary row empties the package - 27088832 and 27066672 both do.
                _move_project_price(project_rows, others, trace.unmatched, ctx)
                cobs = others
        by_id = {item.candidate_id: item for item in cobs}
        groups, nameless = _baseline(cobs, ctx)
        for candidate in nameless:
            trace.unmatched.append({"candidate_id": candidate.candidate_id,
                                    "reason": "missing_object_name"})

        wanted = llm is not None and (_needs_model(cobs) or
                                      (bool(categories) and any(_category_missing(group)
                                                                for group in groups)))
        if not wanted:
            trace.decisions.append({"package_no": package.package_no, "op": "model", "accepted": False,
                                    "reject": "no_model" if llm is None else "nothing_to_disambiguate"})
        prepared.append({
            "package": package, "items": items, "amount": amount, "origin": origin, "raw": raw,
            "ctx": ctx, "cobs": cobs, "by_id": by_id, "groups": groups, "trace": trace,
            "wanted": wanted,
            # Only a non-positive amount is suspicious; the hint itself is not a judgement.
            "suspect_hint": None if amount is None or amount > 0 else "non_positive",
        })

    # Phase 2: the per-package identity call, for the packages that asked for one. The requests are
    # independent - each sees only its own groups and the read-only evidence - so they may overlap.
    asked: dict[str, list[dict]] = {}
    if llm is not None:
        todo = [entry for entry in prepared if entry["wanted"]]
        for entry, produced in zip(todo, parallel_map(
                lambda item: _ask_merge(item["ctx"], item["groups"], item["cobs"], item["amount"],
                                        item["raw"], item["suspect_hint"], evidence, llm, counter,
                                        categories),
                todo)):
            deltas, fallback, note = produced
            asked[entry["package"].package_no] = deltas
            if note:
                notes[entry["package"].package_no] = note
            if fallback:
                entry["trace"].repairs.append({"package_no": entry["package"].package_no,
                                               "action": "merge_model_fallback", "reason": fallback})

    def finish_one(entry: dict, deltas: list[dict]) -> Project:
        """Phase 3 for one package: apply the model's answer and build the project.

        Everything this writes goes into the package's own buffers, which the parent flushes in
        package order, so the audit arrays read exactly as the serial loop wrote them.
        """
        package = entry["package"]
        items, ctx = entry["items"], entry["ctx"]
        by_id, groups = entry["by_id"], entry["groups"]
        amount, origin, raw = entry["amount"], entry["origin"], entry["raw"]
        trace = entry["trace"]
        repairs, decisions = trace.repairs, trace.decisions
        conflicts, unmatched = trace.conflicts, trace.unmatched

        groups, excluded = _apply_deltas(groups, deltas, by_id, ctx, decisions, category_map)
        _donate_excluded(excluded, groups, ctx, decisions)
        for candidate, kind in excluded:
            unmatched.append({"candidate_id": candidate.candidate_id, "reason": kind})

        kept: list[Group] = []
        for group in groups:
            if any(_ownership(item, ctx)[1] for item in group.members):
                kept.append(group)
                continue
            for candidate in group.members:
                unmatched.append({"candidate_id": candidate.candidate_id,
                                  "reason": _ownership(candidate, ctx)[2] or "cannot_own_object"})
        groups = kept

        html_groups = sum(1 for group in groups if any(item.source.source_type == "html" for item in group.members))
        clusters = [_reconcile(group, ctx, by_id, conflicts, decisions) for group in groups]
        clusters = [item for item in clusters if _value(item.base, "object_name")]
        suspect = _suspect_amount(amount, raw)
        reconciling = None if (origin == "attachment" or suspect is not None) else amount
        if reconciling is not None:
            _arbitrate(clusters, reconciling, package.package_no, repairs, decisions)
        else:
            _log_unchosen_sums(clusters, package.package_no, decisions,
                               "sum_unverified_no_package_amount" if amount is None
                               else "sum_unverified_suspect_package_amount")
        alternatives[package.project_id] = _alternatives(clusters)
        list_sources[package.package_no] = ("none" if not clusters else
                                            "html" if html_groups == len(clusters) else
                                            "attachment" if html_groups == 0 else "mixed")
        amount_audit[package.package_no] = {
            "amount_yuan": amount, "raw_text": raw, "origin": origin, "suspect": suspect,
            "reconciled": reconciling is not None,
            "observations": ((evidence.amount_observations or {}).get(package.package_no, [])
                             if evidence is not None else []),
        }

        cobs_out = [_to_cob(cluster) for cluster in clusters]
        if (len(cobs_out) == 1 and cobs_out[0].total_price is None and cobs_out[0].unit_price is None
                and reconciling is not None):
            cobs_out[0].total_price = reconciling
            repairs.append({"package_no": package.package_no, "action": "total_from_package_amount",
                            "value": reconciling})

        subs, sub_ids, sub_conflicts, _ = _merge_entity(items, "sub")
        conflicts.extend(sub_conflicts)
        unique_products = list(dict.fromkeys(cob.product_supplier for cob in cobs_out if cob.product_supplier))
        suppliers = [
            Sub(supplier_name=sub.supplier_name, score=sub.score, is_winner=bool(sub.is_winner),
                cooperative_product_suppliers=unique_products if sub.is_winner else [])
            for sub in subs
        ]
        return Project(
            project_id=package.project_id,
            source_project_no=understanding.source_project_no,
            project_name=understanding.project_name or "",
            package_no=package.package_no,
            purchaser=understanding.purchaser,
            package_total_amount=None if suspect is not None else amount,
            cobs=cobs_out,
            subs=suppliers,
            provenance={
                "cob_candidate_ids": [item.base.candidate_id for item in clusters],
                "price_candidate_ids": [None if item.reading is None else item.reading.label for item in clusters],
                # A summed reading has no single source, so its members are joined rather than
                # nested: provenance stays a flat map of ids, as the contract defines it.
                "price_source_ids": [None if item.reading is None else "+".join(item.reading.sources)
                                     for item in clusters],
                "sub_candidate_ids": sub_ids,
            },
        )

    for entry in prepared:
        projects.append(finish_one(entry, asked.get(entry["package"].package_no, [])))
        trace = entry["trace"]
        repairs.extend(trace.repairs)
        decisions.extend(trace.decisions)
        conflicts.extend(trace.conflicts)
        unmatched.extend(trace.unmatched)

    open_conflicts = [item for item in conflicts if item["field"] != "price_bundle"]
    status = "partial" if open_conflicts or unassigned else "success"
    return MergedProjects(
        run_id=run_id,
        status=status,
        projects=projects,
        amount_origins={key: value for key, value in amount_origins.items() if value},
        amount_audit=amount_audit,
        conflicts=conflicts,
        unmatched_summary_rows=unmatched,
        unassigned_candidates=unassigned,
        list_sources=list_sources,
        alternatives={key: value for key, value in alternatives.items() if value},
        repairs=repairs,
        merge_decisions=decisions,
        merge_notes=notes,
        failures=[],
    )


def _to_cob(cluster: _Cluster) -> Cob:
    base = cluster.base
    bundle = cluster.bundle
    return Cob(
        object_name=str(cluster.name_override or _value(base, "object_name")),
        category_code=_value(base, "category_code"),
        category_name=_value(base, "category_name"),
        category_type=_value(base, "category_type"),
        brand=_value(base, "brand"),
        product_supplier=_value(base, "product_supplier"),
        spec_model=_value(base, "spec_model"),
        unit_price=_number(bundle.get("unit_price")),
        quantity=_number(bundle.get("quantity")),
        unit=None if bundle.get("unit") in (None, "") else str(bundle.get("unit")),
        total_price=_number(bundle.get("total_price")),
    )


def _merge_entity(items: list[Candidate], entity_type: str):
    chosen: dict[tuple, Candidate] = {}
    order: list[tuple] = []
    conflicts: list[dict] = []
    unmatched: list[dict] = []
    keys = SUB_FIELDS
    for candidate in items:
        if candidate.entity_type != entity_type:
            continue
        key = (MEMBER_RE.sub("", _norm_name(_value(candidate, "supplier_name"))),)
        if not key[0]:
            continue
        if key not in chosen:
            chosen[key] = candidate
            order.append(key)
            continue
        current = chosen[key]
        replace = candidate.source_priority > current.source_priority or (
            candidate.source_priority == current.source_priority and _completeness(candidate) > _completeness(current)
        )
        base, extra = (candidate, current) if replace else (current, candidate)
        html_names = [item for item in (current, candidate) if item.source.source_type == "html"]
        if html_names:
            base.fields["supplier_name"].normalized_value = _value(html_names[0], "supplier_name")
        for field_key in keys:
            if field_key == "supplier_name":
                continue
            left = _value(base, field_key)
            right = _value(extra, field_key)
            if left in (None, "") and right not in (None, ""):
                base.fields[field_key].normalized_value = right
                base.fields[field_key].status = "present"
            elif field_key == "is_winner" and left in (None, False, "") and right in (True, "是", "true"):
                base.fields[field_key].normalized_value = right
                base.fields[field_key].status = "present"
            elif left not in (None, "") and right not in (None, "") and not _same(left, right) and field_key != "is_winner":
                base.fields[field_key].status = "conflict"
                conflicts.append(
                    {"field": field_key, "candidate_ids": [base.candidate_id, extra.candidate_id], "values": [left, right]}
                )
        chosen[key] = base
    merged = []
    ids = []
    for key in order:
        candidate = chosen[key]
        ids.append(candidate.candidate_id)
        if not _value(candidate, "supplier_name"):
            continue
        winner = _value(candidate, "is_winner")
        merged.append(
            Sub(
                supplier_name=str(_value(candidate, "supplier_name")),
                score=_value(candidate, "score"),
                is_winner=bool(winner) and str(winner).lower() not in {"false", "否", "0"},
            )
        )
    return merged, ids, conflicts, unmatched


# --------------------------------------------------------------------------- checks

def package_checks(project: Project, amount_checked: bool = True) -> list[dict]:
    """Hard checks go to repair, medium checks need review.

    amount_checked is off when the package total itself came from an attachment: checking line
    prices against an amount read from the same file would only confirm itself.

    The old version compared the line sum to the package amount only when at least one row carried
    a total, so a package of six objects with no prices at all reported success. It also allowed 8%
    of slack in both directions and 5% inside a row; those tolerances had no basis, and amounts are
    copied verbatim, so a cent is the only slack left.
    """
    checks: list[dict] = []
    package_no = project.package_no
    if not project.cobs:
        checks.append({"package_no": package_no, "check": "missing_cob", "level": "hard"})
    if not any(sub.is_winner for sub in project.subs):
        checks.append({"package_no": package_no, "check": "missing_winner", "level": "hard"})
    unpriced = [index for index, cob in enumerate(project.cobs)
                if cob.total_price is None and cob.unit_price is None]
    if unpriced:
        checks.append({"package_no": package_no, "check": "missing_line_price", "level": "medium",
                       "cob_indexes": unpriced})
    totals = [cob.total_price for cob in project.cobs if isinstance(cob.total_price, (int, float))]
    amount = project.package_total_amount if amount_checked else None
    if amount and totals:
        summed = sum(totals)
        if summed - amount > CENT:
            # Adding the rows that carry no price can only push this further up, so a shortfall of
            # evidence does not excuse an overflow.
            checks.append({"package_no": package_no, "check": "amount_overflow", "level": "hard",
                           "line_sum": summed, "package_total": amount})
        elif amount - summed > CENT:
            # A shortfall is never a defect on its own. t20260202_26139731 package 3 is gold at a
            # line sum of 1579600 against an amount of 2431310: the announcement's object list
            # simply does not cover the whole award. What is actionable is a row with no price at
            # all, and missing_line_price already reports that, so this stays information only.
            checks.append({"package_no": package_no, "check": "amount_underflow", "level": "soft",
                           "line_sum": summed, "package_total": amount})
    return checks


def amount_checked(merged: MergedProjects, project: Project) -> bool:
    return merged.amount_origins.get(project.package_no) != "attachment"


def consistency_checks(merged: MergedProjects) -> list[Failure]:
    checks: list[dict] = []
    for project in merged.projects:
        checks.extend(package_checks(project, amount_checked=amount_checked(merged, project)))
    merged.checks = checks
    blocking = [item for item in checks if item["level"] in {"hard", "medium"}]
    if not blocking:
        return []
    if merged.status == "success":
        merged.status = "partial"
    return [
        Failure(
            failure_code="consistency_check_failed",
            failure_message="；".join(f"{item['package_no']}:{item['check']}" for item in blocking)[:500],
        )
    ]
