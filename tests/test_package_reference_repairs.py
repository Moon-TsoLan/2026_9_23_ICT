"""Regression counterexamples for original-number / internal-reference boundaries.

All model answers are fixed in memory. Network access is forbidden in this module.
"""
import json
import socket

import pytest

from ict.bindings import bind_packages
from ict.candidates import seal_candidate
from ict.html_context import ParsedNotice, _package_anchors, fill_package_amounts
from ict.llm import FakeLLMClient, LLMResult, load_prompt
from ict.packages import hint_reference, package_context
from ict.schemas import AttachmentIndex, FileDecision, FileDecisions, HtmlTables, IndexedFile
from ict.steps.s01_understand import seal
from ict.steps.s03_plan import plan_projects
from ict.steps.s04_attach import coerce_package_scope, locate_pages, page_packages
from db.load_runs import _package_base_key, plan_rounds, target_entries


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("network forbidden in offline reference regression")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)


def unit(kind, number, name=None):
    return {"package_kind": kind, "package_no": number, "package_name": name or str(number)}


def understanding(rows):
    return seal("r", {"project_name": "采购", "source_project_no": "P",
        "package_mode": "single" if len(rows) == 1 else "multi", "packages": rows}, None, [])


def notice(sections=()):
    return ParsedNotice("aid", "采购公告", {}, [], [], [], sections=list(sections),
                        package_anchors=_package_anchors(list(sections)))


class CapturingModel(FakeLLMClient):
    def __init__(self, step, answer):
        super().__init__({step: json.dumps(answer, ensure_ascii=False)})
        self.payloads = []

    def complete(self, **kwargs):
        payload, _ = json.JSONDecoder().raw_decode(kwargs["user"])
        self.payloads.append(payload)
        return super().complete(**kwargs)


def test_single_lot_original_number_survives_entire_pipeline(tmp_path, monkeypatch):
    from ict.pipeline import run_announcement
    html = '''<html><body><div class="vF_detail_content"><p>一、采购结果</p><p>第1标段中标供应商：甲公司。</p>
    <table><tr><th>名称</th><th>数量</th><th>单价</th></tr>
    <tr><td>设备</td><td>1</td><td>100元</td></tr></table></div></body></html>'''
    (tmp_path / "aid.html").write_text(html, encoding="utf-8")

    class Model:
        def complete(self, *, step, prompt_version, user, **kwargs):
            payload, _ = json.JSONDecoder().raw_decode(user)
            if step == "understand_announcement":
                answer = {"project_name": "采购", "package_mode": "single",
                          "packages": [unit("lot", "1", "第1标段")]}
            elif step == "understand_html_tables":
                assert prompt_version == "html-tables-v6"
                answer = {"table_role": "cob_detail", "package_scope": "lot:1", "row_grain": "cob",
                          "column_mapping": {"object_name": "名称", "quantity": "数量", "unit_price": "单价"}}
            elif step == "extract_html_candidates":
                assert payload["known_packages"] == ["lot:1"]
                assert payload["package_registry"][0]["source_package_no"] == "1"
                if payload.get("body_sections"):
                    fields = {"supplier_name": "甲公司", "is_winner": True, "bid_amount": "100元"}
                    entry = {"entity_type": "sub", "fields": fields, "source_refs": ["html:body:0"]}
                else:
                    fields = {"object_name": "设备", "quantity": 1, "unit": "台", "unit_price": "100元",
                              "total_price": "100元", "product_supplier": "制造厂"}
                    entry = {"entity_type": "cob", "fields": fields, "source_refs": ["html:t0:r0"]}
                # The old-number response was accepted before but then lost in the singleton branch.
                answer = {"candidates": [{**entry, "package_no": "1"}]}
            elif step == "merge_candidates":
                answer = {"deltas": []}
            else:
                raise AssertionError("unexpected call: " + step)
            return LLMResult(json.dumps(answer, ensure_ascii=False), "fixed", "fixed", prompt_version, 0)

    monkeypatch.setattr("ict.pipeline.load_index", lambda *args: None)
    report = run_announcement("aid", llm=Model(), html_dir=tmp_path,
                              attachments_root=tmp_path / "absent", runs_root=tmp_path / "runs")
    project = report.result["packages"][0]
    assert project["package_no"] == "lot:1" and project["source_package_no"] == "1"
    assert [row["object_name"] for row in project["cobs"]] == ["设备"]
    assert [row["supplier_name"] for row in project["subs"]] == ["甲公司"]
    audit = json.loads((tmp_path / "runs/aid/03b_package_bindings.json").read_text(encoding="utf-8"))
    assert not audit["unresolved"]
    assert all(row["suggested_package_no"] == "1" for row in audit["bindings"])


def test_singleton_does_not_accept_unrelated_original_number():
    u = understanding([unit("lot", "1")])
    c = seal_candidate(candidate_id="c", entity_type="cob", project_id="采购|9", package_no="9",
                       source_type="html", file_id=None, source_priority=100, raw_fields={"object_name": "设备"})
    audit, errors = bind_packages(u, [c], notice(), None, None)
    assert not errors and audit["unresolved"]
    assert c.package_no == "9"  # No invented assignment to the only registered unit.


def test_unique_original_number_amount_anchor_reaches_typed_directory():
    u = understanding([unit("lot", "1"), unit("lot", "2")])
    n = notice([{"title": "结果", "text": "第1包 中标金额100元"},
                {"title": "结果", "text": "第2包 中标金额200元"}])
    fill_package_amounts(u, n)
    assert [p.package_amount.amount_yuan for p in u.packages] == [100, 200]
    assert [p.package_no for p in u.packages] == ["lot:1", "lot:2"]


@pytest.mark.parametrize("choose", [True, False])
def test_ambiguous_anchor_is_not_copied_to_both_units_and_reaches_model(choose):
    u = understanding([unit("lot", "1"), unit("contract_package", "1")])
    n = notice([{"title": "结果", "text": "第1包 中标金额100元"}])
    fill_package_amounts(u, n)
    assert all(p.package_amount is None for p in u.packages)
    ref = "amount:html:anchor:0"
    model = CapturingModel("bind_packages", {"bindings": [], "observation_bindings": [], "amount_bindings": [
        {"package_no": p.package_no, "amount_ref": ref if choose and p.package_kind == "contract_package" else None,
         "amount_kind": "award", "reason": "原文合同包" if choose else "不能确定"} for p in u.packages]})
    audit, errors = bind_packages(u, [], n, HtmlTables(run_id="r", status="success"), model)
    assert not errors and len(model.payloads) == 1  # Must not take the no-candidate early return.
    payload = model.payloads[0]
    observation = next(row for row in payload["amount_observations"] if row["amount_ref"] == ref)
    assert observation["suggested_package_no"] == "1" and observation["amount_kind"] == "unknown"
    assert any(row["source_ref"] == observation["source_ref"] and "100元" in row["text"]
               for row in payload["source_rows"])
    assert audit["html_anchor_amounts"][0]["amount_ref"] == ref
    assert u.packages[0].package_amount is None
    if choose:
        assert u.packages[1].package_amount.amount_yuan == 100
        assert u.packages[1].package_amount.source_ref == "html:anchor:0"
        # Attachment arrival reuses the binder. The adopted anchor is now represented by
        # the package amount and must not become a second independently assignable amount.
        again = CapturingModel("bind_packages", {"bindings": [], "observation_bindings": [], "amount_bindings": [
            {"package_no": p.package_no,
             "amount_ref": "amount:package:contract_package:1" if p.package_kind == "contract_package" else None,
             "amount_kind": "award", "reason": "保留原归属"} for p in u.packages]})
        _, errors = bind_packages(u, [], n, HtmlTables(run_id="r", status="success"), again, previous=audit)
        assert not errors
        assert len([item for item in again.payloads[0]["amount_observations"]
                    if item.get("source_ref") == "html:anchor:0"]) == 1
    else:
        assert u.packages[1].package_amount is None


def locate(u, text, scope, possible=(), filename="合订文件.pdf"):
    plans = plan_projects("r", u, [], True)
    files = FileDecisions(run_id="r", status="success", file_decisions=[FileDecision(file_id="f",
        file_class="bid_quote", priority=1, read_strategy="target_pages", reason="fixed", possible_packages=list(possible))])
    index = AttachmentIndex(announcement_id="aid", files=[IndexedFile(file_id="f", display_name=filename,
        relative_path="f.pdf", extension="pdf", readability="text_extractable")])
    page = {"page_no": 1, "text": text, "chars": len(text), "tables": []}
    model = CapturingModel("locate_pages", {"page_decisions": [{"file_id": "f", "page_no": 1,
        "package_scope": scope, "extraction_mode": "text", "relevance": 1, "reason": "fixed"}]})
    return locate_pages("r", plans, files, {"f": [page]}, index, model).page_decisions[0].package_scope


def test_mixed_directory_restores_multi_package_page_scope():
    u = understanding([unit("lot", "1"), unit("contract_package", "1"), unit("contract_package", "2")])
    text = "第1包 产品甲；第2包 产品乙。分项报价表与中标供应商信息。"
    assert locate(u, text, "2") == "announcement"
    # A lone ambiguous literal number also cannot force one of the same-number units.
    assert locate(u, "第1包 产品甲。原文分项报价表信息，详见表格。", "1") == "announcement"


@pytest.mark.parametrize("possible,filename", [(["2"], "合订文件.pdf"), ([], "第2包报价.pdf")])
def test_one_typed_sibling_does_not_disable_ordinary_package_hint(possible, filename):
    u = understanding([unit("lot", "1"), unit("contract_package", "2")])
    assert locate(u, "最终报价如下，供应商及产品信息详见报价表。", "unknown", possible, filename) == "2"


def test_unique_original_page_number_and_filename_resolve_to_reference():
    u = understanding([unit("lot", "1"), unit("lot", "2")])
    refs = [p.package_no for p in u.packages]
    assert page_packages("第1包 产品甲；第2包 产品乙", refs, u.packages) == set(refs)
    assert coerce_package_scope("1", [], refs, "文件.pdf", u.packages) == "lot:1"
    assert locate(u, "供应商产品最终报价，见本页的详细表格。", "unknown", filename="第1包报价.pdf") == "lot:1"


def test_ambiguous_filename_never_selects_first_same_number_unit():
    u = understanding([unit("lot", "1"), unit("contract_package", "1")])
    assert hint_reference("1", u.packages) is None
    assert locate(u, "供应商产品最终报价，见本页的详细表格。", "unknown", filename="第1包报价.pdf") == "unknown"


def test_cross_announcement_key_does_not_depend_on_siblings():
    a = understanding([unit("contract_package", "1")]).packages[0]
    b = understanding([unit("lot", "1"), unit("contract_package", "1")]).packages[1]
    keys = [_package_base_key(p.model_dump() | {"source_project_no": "P"}) for p in (a, b)]
    assert keys == ["P|contract_package|1"] * 2
    plan = plan_rounds([{"aid": aid, "date": aid[1:9], "index": 0, "package_key": key}
                        for aid, key in zip(["t20261001_a", "t20261002_b"], keys)])
    assert plan.inserts[("t20261002_b", 0)][1] == 2


def test_unnumbered_units_do_not_become_other_units_rounds_when_order_changes(tmp_path):
    runs = []
    for aid, names in [("t20261001_a", ["网络采购", "安全采购"]), ("t20261002_b", ["安全采购", "网络采购"])]:
        u = understanding([unit("contract_package", None, name) for name in names])
        root = tmp_path / aid
        root.mkdir()
        (root / "10_run_report.json").write_text(json.dumps({"announcement_id": aid}), encoding="utf-8")
        projects = [p.model_dump() | {"source_project_no": "P", "project_name": "采购"} for p in u.packages]
        (root / "09_merged_projects.json").write_text(json.dumps({"projects": projects}), encoding="utf-8")
        runs.append(root)
    entries = target_entries(runs)
    assert len({e["package_key"] for e in entries}) == 4
    assert all(row[1] == 1 for row in plan_rounds(entries).inserts.values())


def test_same_type_duplicate_original_numbers_are_announcement_local():
    u = understanding([unit("contract_package", "1", "网络采购"), unit("contract_package", "1", "安全采购")])
    packages = [p.model_dump() | {"source_project_no": "P"} for p in u.packages]
    keys = [_package_base_key(p, "aid", packages) for p in packages]
    assert len(set(keys)) == 2
    assert all("local:aid" in key for key in keys)


def test_local_business_key_requires_announcement_context():
    p = understanding([unit("contract_package", None, "设备采购")]).packages[0].model_dump()
    with pytest.raises(ValueError, match="announcement_id"):
        _package_base_key(p)


def test_online_prompt_contracts_do_not_request_original_output_numbers():
    html = load_prompt("html-candidates-v5")
    tables = load_prompt("html-tables-v5")
    assert "包号保留原编号" not in html
    assert "归属编号用原文里能确定的写法" not in tables
    for text in (html, tables, load_prompt("package-binding-v4")):
        assert "采购单元内部引用" in text

