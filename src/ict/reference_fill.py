"""Fill existing project/COB gaps from explicit labels and related cells in reference files."""
from collections import defaultdict
from pathlib import PurePath
import re

from ict.file_routing import compact
from ict.money import parse_quantity
from ict.schemas import MergedProjects


PACKAGE_NO = ("包号", "包编号", "合同包号", "采购包编号", "标段号", "标段编号", "标项编号")
PACKAGE_NAME = ("包名", "包名称", "分包名称", "采购包名称", "合同包名称", "标段名称", "标项名称")
OBJECT = ("标的名称", "采购标的名称", "货物名称", "产品名称", "服务名称", "工程名称")
IDENTITY_LINE = re.compile(r"^\s*(?:[一二三四五六七八九十\d]+[、.．]\s*)?(?:采购)?(项目名称|项目编号)\s*[：:]\s*(.+?)\s*$")
PACKAGE_TITLE = re.compile(r"^\s*((?:第?[零〇一二三四五六七八九十百\dA-Z]+(?:合同包|包|标段)|(?:合同包|包|标段)\s*[零〇一二三四五六七八九十百\dA-Z]+))\s*名称\s*[：:]\s*(.+?)\s*$")


def cleaned(raw):
    value = re.sub(r"[\r\n]+", "", str(raw or "")).strip()
    if not value or re.fullmatch(r"[/＿_\-—*….\s]+", value) or re.search(r"【.*?填写.*?】|请填写|^\s*[（(].*?[）)]\s*$", value):
        return ""
    return value


def ordinal(raw):
    key = compact(str(raw or "")).upper()
    match = re.fullmatch(r"(?:第)?(?:合同包|包|标段|标项)?([零〇一二三四五六七八九十\dA-Z]+)(?:合同包|包|标段|标项)?", key)
    if not match:
        return key
    value = match[1]
    if value.isdecimal():
        return str(int(value))
    digits = dict(zip("零一二三四五六七八九", range(10)), 〇=0)
    if all(c in digits or c == "十" for c in value):
        left, sep, right = value.partition("十")
        if sep and len(left) <= 1 and len(right) <= 1:
            return str(10 * digits.get(left, 1) + digits.get(right, 0))
        if not sep:
            return str(int("".join(str(digits[c]) for c in value)))
    return value


def match_package(projects, number, header):
    if not number:
        return None
    kind = "lot" if "标段" in header + number else "contract_package" if "包" in header + number else None
    matches = [i for i, p in enumerate(projects)
               if (not kind or p.package_kind in (kind, "unknown"))
               and ordinal(p.source_package_no or p.package_no) == ordinal(number)]
    return matches[0] if len(matches) == 1 else None


def related_list(tree, project, index, field, path):
    ids = (project.provenance or {}).get("cob_candidate_ids", [])
    if index >= len(ids):
        return False
    candidate = next((c for c in tree["candidates"] if c["candidate_id"] == ids[index]), None)
    if candidate is None:
        return False
    observation = candidate["fields"].get(field, {})
    if observation.get("status") != "points_to_attachment":
        return False
    pointer = compact(str(observation.get("raw_value", "")))
    if PurePath(path).name in pointer:
        return True
    references = [f for f in tree["files"] if f["route"] == "reference"]
    return (len(references) == 1 and
            any(word in pointer for word in ("招标文件", "采购文件", "磋商文件", "采购清单", "招标清单")))


def fill_reference_fields(tree, merged: MergedProjects) -> MergedProjects:
    if merged.run_id != tree["announcement_id"]:
        raise ValueError("采购单元与第三类文件必须来自同一公告")
    result = merged.model_copy(deep=True)
    projects = result.projects
    options = defaultdict(dict)
    conflicts = {c.get("field") for c in tree.get("html_conflicts", [])}

    def offer(project_index, cob_index, field, value, source):
        if value in ("", None):
            return
        project = projects[project_index]
        if cob_index is not None:
            base = (project.provenance or {}).get("cob_candidate_ids", [])
            cid = base[cob_index] if cob_index < len(base) else None
            if any(x.get("field") == field and
                   (cid and cid in x.get("candidate_ids", []) or x.get("package_no") == project.package_no)
                   for x in (*merged.conflicts, *merged.unresolved)):
                return
        target = project if cob_index is None else project.cobs[cob_index]
        current = getattr(target, field)
        if field == "package_name" and ordinal(current) == ordinal(project.source_package_no or project.package_no):
            current = None
        if current not in ("", None):
            return
        key = (project_index, cob_index, field)
        options[key].setdefault(compact(str(value)), (value, []))[1].append(source)

    missing_identity = any(not tree["identity"].get(k) and k not in conflicts
                           for k in ("project_name", "source_project_no"))
    if not missing_identity and all(
        p.package_name and p.source_package_no and ordinal(p.package_name) != ordinal(p.source_package_no)
        and all(c.category_name and c.spec_model and c.quantity is not None for c in p.cobs) for p in projects
    ):
        return result
    for attachment in tree["files"]:
        if attachment["route"] != "reference":
            continue
        path = attachment["path"]
        for part in attachment["parts"]:
            ref = f"{path}#{part['number']}"
            for line_number, line in enumerate(part["text"].splitlines(), 1):
                match = IDENTITY_LINE.fullmatch(line)
                if match:
                    field = "project_name" if match[1] == "项目名称" else "source_project_no"
                    value = cleaned(match[2])
                    if field not in conflicts and not tree["identity"].get(field) and (field != "source_project_no" or re.fullmatch(r"[\w()（）./－—\-]+", value)):
                        for i in range(len(projects)):
                            offer(i, None, field, value, f"{ref}:L{line_number}")
                title = PACKAGE_TITLE.fullmatch(line)
                if title:
                    i = match_package(projects, title[1], "")
                    if i is not None:
                        offer(i, None, "package_name", cleaned(title[2]), f"{ref}:L{line_number}")
            for ti, table in enumerate(part.get("tables", []), 1):
                headers = [compact(str(h or "")) for h in table["headers"]]
                for ri, row in enumerate(table["rows"], 1):
                    cells = dict(zip(headers, map(cleaned, row)))

                    def cell(keys):
                        return next(((h, cells[h]) for h in keys if cells.get(h)), ("", ""))

                    number_header, number = cell(PACKAGE_NO)
                    i = match_package(projects, number, number_header)
                    rowref = f"{ref}:T{ti}R{ri}"
                    if i is None and number:
                        _, name = cell(PACKAGE_NAME)
                        same = [k for k, p in enumerate(projects) if name and p.package_name and
                                compact(p.package_name) == compact(name) and not p.source_package_no
                                and not (p.package_kind == "lot" and "包" in number_header)
                                and not (p.package_kind == "contract_package" and "标段" in number_header)]
                        if len(same) == 1:
                            i = same[0]
                            offer(i, None, "source_package_no", number, f"{rowref}:{number_header}")
                    if i is not None:
                        h, name = cell(PACKAGE_NAME)
                        offer(i, None, "package_name", name, f"{rowref}:{h}")
                    elif len(projects) == 1 and not any(h in PACKAGE_NO for h in headers):
                        i = 0
                    else:
                        continue
                    project = projects[i]
                    _, name = cell(OBJECT)
                    indices = [j for j, c in enumerate(project.cobs) if compact(c.object_name) == compact(name)]
                    if len(indices) != 1:
                        continue
                    j = indices[0]
                    h, value = cell(("品目名称", "采购品目", "品目"))
                    offer(i, j, "category_name", value, f"{rowref}:{h}")
                    if related_list(tree, project, j, "spec_model", path):
                        h, value = cell(("规格型号", "规格、型号", "规格", "型号", "服务规格"))
                        offer(i, j, "spec_model", value, f"{rowref}:{h}")
                    if related_list(tree, project, j, "quantity", path):
                        h, value = cell(("计价数量", "采购数量", "数量"))
                        quantity, inline_unit, ok = parse_quantity(value)
                        _, unit = cell(("计价单位", "单位"))
                        known = project.cobs[j].unit
                        if ok and known and (unit or inline_unit) and all(
                            compact(v) == compact(known) for v in (unit, inline_unit) if v
                        ):
                            offer(i, j, "quantity", quantity, f"{rowref}:{h}")
    for (i, j, field), values in options.items():
        if len(values) != 1:
            continue
        value, refs = next(iter(values.values()))
        target = projects[i] if j is None else projects[i].cobs[j]
        setattr(target, field, value)
        result.field_bindings.append({"stage": "reference_fill", "package_no": projects[i].package_no,
                                      "cob_index": j, "field": field, "value": value, "sources": refs})
    return result

