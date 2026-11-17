"""Offline regression of the three redesign stages, including the fixed HTML outputs."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from ict.file_routing import Content, Document, classify, read_file
from ict.redesign import merge_package_objects
from ict.reference_fill import fill_reference_fields
from ict.schemas import MergedProjects, Project, Cob
from ict.unit_merge import apply_unit_decisions, decision_context


FIXED = Path(__file__).resolve().parents[1] / "eval/fixtures/attachment-redesign-20261117/fixed"


def fixture(case):
    return json.loads((FIXED / f"{case}.json").read_text(encoding="utf-8"))


def test_second_class_and_ocr_decision():
    doc = Document(Path("扫描件.png"), [Content(1, "", True)])
    assert classify(doc)["route"] == "needs_ocr"
    assert classify(doc, {1: "专家费支付表 王甲 评审劳务报酬"})["route"] == "irrelevant"
    assert classify(doc, {1: "页眉"})["route"] == "undetermined"
    assert classify(doc, {1: "中标结果公告 中标供应商 北京甲乙公司 中标金额5万元"})["route"] == "direct"
    # The typed and recognized sections support the classification together.
    doc.parts[0].text = "评审情况表"
    assert classify(doc, {1: "北京甲乙公司 符合性审查通过"})["route"] == "direct"


def test_bad_pdf_and_scan_distinction(tmp_path):
    path = tmp_path / "明细.pdf"
    path.write_bytes(b"%PDF-1.7\nbad file")
    assert classify(read_file(path))["route"] == "unavailable"
    from PIL import Image
    Image.new("RGB", (10, 10)).save(path, format="PNG")
    assert classify(read_file(path))["route"] == "needs_ocr"


def test_fixed_virtual_units_move_atomically():
    tree = fixture("t20260309_26250037")
    assert len(tree["units"]) == len(tree["virtual_units"]) == 2
    assert [len(v["candidate_ids"]) for v in tree["virtual_units"]] == [2, 2]
    context = decision_context(tree)
    decisions = []
    for branch in context["branches"]:
        ref = branch["ref"]
        target = {"virtual:cand_000002": "unit:1", "virtual:cand_000001": "unit:2"}.get(ref, ref)
        decisions.append(dict(source=ref, target=target, reason="冻结来源对应", source_refs=["分项报价表.pdf#6"]))
    result = apply_unit_decisions(tree, {"decisions": decisions}, context)
    assert result["virtual_units"] == []
    assert result["units"][0]["candidate_ids"] == ["cand_000002", "cand_000004"]
    assert all(c["package_no"] for c in result["candidates"])
    assert not tree["units"][0]["candidate_ids"]
    unresolved = deepcopy(decisions)
    unresolved[-1]["target"] = None
    remaining = apply_unit_decisions(tree, {"decisions": unresolved}, context)
    assert len(remaining["virtual_units"]) == 1
    unresolved[-1]["target"] = "unit:3"
    with pytest.raises(ValueError):
        apply_unit_decisions(tree, {"decisions": unresolved}, context)


def test_alias_can_merge_into_full_name_without_merging_a_lot_and_a_package():
    tree = fixture("t20260309_26250037")
    alias = deepcopy(tree["units"][0])
    alias.update(package_no="alias", package_name="设备包")
    tree["units"].append(alias)
    context = decision_context(tree)
    decisions = [dict(source=b["ref"], target=None if b["virtual"] else b["ref"],
                      reason="见设备采购原文", source_refs=["html:n54"]) for b in context["branches"]]
    next(d for d in decisions if d["source"] == "alias")["target"] = "unit:1"
    assert len(apply_unit_decisions(tree, {"decisions": decisions}, context)["units"]) == 2
    alias["package_kind"] = "lot"
    with pytest.raises(ValueError):
        apply_unit_decisions(tree, {"decisions": decisions}, decision_context(tree))


@pytest.mark.parametrize(("case", "names"), [
    ("t20260511_26545535", ["A 包：教师业绩智能服务平台", "B 包：人事管理系统", "C 包：科研管理服务平台"]),
    ("t20260206_26154828", ["第1包", "第2包", "第3包"]),
])
def test_reference_fill_from_frozen_html_uses_existing_package_merge(case, names):
    tree = fixture(case)
    before = merge_package_objects(tree)
    after = fill_reference_fields(tree, before)
    assert [p.package_name for p in after.projects] == names
    for left, right in zip(before.projects, after.projects):
        assert left.model_dump(exclude={"package_name"}) == right.model_dump(exclude={"package_name"})
    changed = [b for b in after.field_bindings if b.get("stage") == "reference_fill"]
    if case == "t20260511_26545535":
        assert len(changed) == 3
        assert all("#4:" in b["sources"][0] for b in changed)
    else:
        assert not changed


def test_reference_object_fields_require_same_row_and_explicit_pointer():
    project = Project(project_id="p", package_no="第1包", source_package_no="第1包", project_name="",
                      provenance={"cob_candidate_ids": ["c1"]},
                      cobs=[Cob(object_name="监控器", unit="台")])
    merged = MergedProjects(run_id="a", status="success", projects=[project])
    part = {"number": 1, "text": "项目名称：建设工程\n项目编号：2026-A", "tables": [{
        "headers": ["包号", "包名称", "标的名称", "采购品目", "规格型号", "数量", "单位", "最高限价"],
        "rows": [["包01", "设备包", "监控器", "视频设备", "M1", "5", "台", "999"],
                 ["包2", "无关包", "监控器", "其他", "M2", "3", "台", "99"]]}]}
    pointer = {"raw_value": "见采购文件清单", "status": "points_to_attachment"}
    candidate = {"candidate_id": "c1", "fields": {"spec_model": pointer, "quantity": pointer}}
    tree = {"announcement_id": "a", "identity": {}, "html_conflicts": [], "candidates": [candidate],
            "files": [{"path": "采购文件.pdf", "route": "reference", "parts": [part]}]}
    result = fill_reference_fields(tree, merged).projects[0]
    assert (result.project_name, result.source_project_no, result.package_name) == ("建设工程", "2026-A", "设备包")
    cob = result.cobs[0]
    assert (cob.category_name, cob.spec_model, cob.quantity, cob.total_price) == ("视频设备", "M1", 5, None)
    part["tables"][0]["rows"].append(["第1包", "另一包名", "监控器", "视频设备", "M8", "4", "套", "600"])
    candidate["fields"].clear()
    tree["html_conflicts"] = [{"field": "project_name"}]
    result = fill_reference_fields(tree, merged).projects[0]
    assert not result.project_name and not result.package_name
    assert result.cobs[0].spec_model is None and result.cobs[0].quantity is None
    part["tables"][0]["headers"][4] = "服务范围"
    candidate["fields"]["spec_model"] = pointer
    assert fill_reference_fields(tree, merged).projects[0].cobs[0].spec_model is None

