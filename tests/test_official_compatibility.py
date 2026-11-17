"""Offline contracts for official-format compatibility. No HTTP/model/OCR calls."""
import json
from copy import deepcopy
from io import BytesIO

import pytest
from openpyxl import load_workbook

from ict.candidates import seal_candidate
from ict.catalog import Catalog
from ict.official import export_workbook, official_rows, money_wan, read_gold_workbook, score_gold_cell
from ict.packages import package_label, registry_references, hint_reference
from ict.schemas import AnnouncementUnderstanding, PackageUnderstanding, Project, Sub, MergedProjects, ProjectPlans, ProjectPlan, FileDecisions, FileDecision
from ict.steps.s01_understand import seal
from ict.steps.s03_plan import plan_projects
from ict.steps.s07_normalize import normalize_candidates
from ict.steps.s08_merge import _merge_entity, _value
from ict.steps.s08b_repair import repair_packages
from ict.sub_merge import merge_subs
from ict.llm import FakeLLMClient


def observation(cid, fields, ref=None):
    return seal_candidate(candidate_id=cid, entity_type="sub", project_id="采购|1", package_no="1",
        source_type="html", file_id=None, source_priority=80, raw_fields=fields,
        source_refs=[ref] if ref else [])


def normalized(*rows):
    return normalize_candidates("offline", list(rows), Catalog()).candidates


def understanding():
    return AnnouncementUnderstanding(run_id="r", status="success", project_name="采购", announcement_type="winning_announcement",
        package_mode="single", packages=[PackageUnderstanding(package_no="1", project_id="采购|1", package_evidence_text="原文",
            package_kind="contract_package", package_name="设备采购包", source_package_no="1")])


def test_same_number_lot_and_contract_keep_distinct_ids_and_labels():
    proposed = {"project_name": "采购", "package_mode": "multi", "packages": [
        {"package_no": "1", "package_kind": "lot", "package_name": "一标段"},
        {"package_no": "1", "package_kind": "contract_package", "package_name": "设备采购包"}]}
    result = seal("r", proposed, None, [])
    assert len(result.packages) == 2
    assert len({p.project_id for p in result.packages}) == 2
    assert [package_label(p) for p in result.packages] == ["一标段", "设备采购包"]
    assert hint_reference("1", result.packages) is None


def test_missing_and_duplicate_number_never_silently_discard_units():
    rows = [{"package_no": None}, {"package_no": "1"}, {"package_no": "1"}, {"package_no": "unit:1"}]
    refs = [ref for ref, original in registry_references(rows)]
    assert len(refs) == len(set(refs)) == 4


def test_single_package_does_not_mean_unpartitioned():
    p = understanding().packages[0]
    assert package_label(p) == "设备采购包"
    p.package_kind = "unknown"; p.package_name = None
    assert package_label(p) is None
    p.package_kind = "unpartitioned"
    assert package_label(p) == "未分包"


def test_supplier_gap_targets_supplier_and_opens_attachments():
    row = observation("s1", {"supplier_name": "乙", "review_status": "failed"})
    plan = plan_projects("r", understanding(), [row], True).projects[0]
    assert plan.needs
    for field in ("bid_amount", "review_failure_reason"):
        need = next(n for n in plan.needs if n["field"] == field)
        assert need["target_candidate_ids"] == ["s1"]
        assert need["package_no"] == "1"
    assert any(n["field"] == "supplier_name" and n["reason"] == "maybe_more_objects" for n in plan.needs)


def test_zero_quote_is_present_and_score_does_not_prove_review():
    row = observation("s1", {"supplier_name": "甲", "bid_amount": 0, "score": 91.02, "is_winner": True})
    plan = plan_projects("r", understanding(), [row], True).projects[0]
    assert not any(n["field"] == "bid_amount" for n in plan.needs)
    assert not any(n["field"] == "review_status" for n in plan.needs)


def test_supplier_quote_and_award_amount_normalize_independently():
    row = normalized(observation("s1", {"supplier_name": "乙", "bid_amount": "12.5万元", "supplier_award_amount": "10万元",
        "review_status": "failed", "review_failure_reason": "资质不符合要求", "score": 91.02}))[0]
    assert _value(row, "bid_amount") == 125000
    assert _value(row, "supplier_award_amount") == 100000
    assert _value(row, "review_status") == "failed"
    assert _value(row, "score") == 91.02
    row = observation("s2", {"supplier_name": "乙", "bid_amount": 12.5})
    from ict.schemas import CandidateEvidence
    row.evidence = CandidateEvidence(column_units={"bid_amount": "最终报价（万元）"})
    assert _value(normalized(row)[0], "bid_amount") == 125000


def test_single_supplier_fallback_preserves_new_fields():
    row = normalized(observation("s1", {"supplier_name": "乙", "bid_amount": "120元", "review_status": "failed",
        "review_failure_reason": "资质不符"}))[0]
    subs, _, _, _ = _merge_entity([row], "sub")
    assert subs[0].bid_amount == 120 and subs[0].review_failure_reason == "资质不符"


def test_fallback_conflicting_quotes_and_review_remain_unknown():
    rows = normalized(observation("s1", {"supplier_name": "甲", "bid_amount": "100元", "review_status": "passed"}),
                      observation("s2", {"supplier_name": "甲", "bid_amount": "120元", "review_status": "failed", "review_failure_reason": "不符"}))
    subs, _, conflicts, _ = _merge_entity(rows, "sub")
    assert subs[0].bid_amount is None and subs[0].review_status == "unknown"
    assert subs[0].review_failure_reason is None
    assert any(c["reason"] == "sub_fallback_new_field_unresolved" for c in conflicts if "reason" in c)


def test_model_selects_final_quote_and_keeps_failed_supplier():
    rows = normalized(observation("s1", {"supplier_name": "甲", "bid_amount": "100元"}),
                      observation("s2", {"supplier_name": "甲", "bid_amount": "90元"}),
                      observation("s3", {"supplier_name": "乙", "review_status": "failed", "review_failure_reason": "资质不符"}))
    answer = {"groups": [
        {"candidate_ids": ["s1", "s2"], "name_from": "s1", "bid_amount_from": "s2", "winner": True, "reason": "最终响应报价90元"},
        {"candidate_ids": ["s3"], "name_from": "s3", "review_from": "s3", "review_reason_from": "s3", "winner": False, "reason": "未通过"}], "excluded": []}
    model = FakeLLMClient({"merge_subs": json.dumps(answer)})
    result = merge_subs(rows, "1", model, None, _value)
    assert result["accepted"]
    assert result["subs"][0].bid_amount == 90
    assert result["subs"][1].review_status == "failed"


def test_model_cannot_drop_observed_quote_without_explaining():
    rows = normalized(observation("s1", {"supplier_name": "甲", "bid_amount": "100元"}), observation("s2", {"supplier_name": "甲"}))
    answer = {"groups": [{"candidate_ids": ["s1", "s2"], "name_from": "s1", "winner": True, "reason": "忽略报价"}]}
    result = merge_subs(rows, "1", FakeLLMClient({"merge_subs": json.dumps(answer)}), None, _value)
    assert not result["accepted"]


def test_repair_does_not_reinstate_failed_winner():
    project = Project(project_id="p", project_name="采购", package_no="1", subs=[Sub(supplier_name="乙", is_winner=False, review_status="failed", review_failure_reason="资质不符")])
    merged = MergedProjects(run_id="r", status="partial", projects=[project])
    repair_packages(merged, {"1": ["乙"]}, None)
    assert merged.projects[0].subs[0].is_winner is False
    assert merged.projects[0].subs[0].review_failure_reason == "资质不符"


def example_result():
    return {"announcement_id": "公告_含_下划线", "packages": [{"project_id": "p", "package_no": "1", "package_kind": "lot", "package_name": "一标段",
        "project_name": "采购", "purchaser": "采购人", "package_total_amount": 300,
        "cobs": [{"object_name": "=定制产品", "spec_model": "定制", "brand": None, "product_supplier": "厂家", "quantity": 254,
                  "unit": "台", "unit_price": 930, "total_price": 236220, "category_name": "最细品目", "category_code": "A02010101", "category_type": "A"}],
        "subs": [{"supplier_name": "甲", "is_winner": True, "supplier_award_amount": 100, "bid_amount": 110},
                 {"supplier_name": "乙", "is_winner": True},
                 {"supplier_name": "丙", "is_winner": False, "review_status": "failed", "review_failure_reason": "资质不符", "score": 91.02}]}]}


@pytest.mark.parametrize("value, expected", [(None,None),(0,0),(5,0),(50,0.01),(150,0.02),(930,0.09),(236220,23.62),(-50,-0.01)])
def test_wan_half_up(value, expected):
    assert money_wan(value) == expected


def test_three_sheet_export_has_required_colors_numbers_and_original_fields(tmp_path):
    original = example_result(); snapshot = deepcopy(original)
    data = export_workbook([original]); w = load_workbook(BytesIO(data))
    assert w.sheetnames == ["总表", "标的表", "投标人表"]
    assert w["总表"].max_row == 3
    assert w["总表"]["H2"].value == 0.01 and w["总表"]["H3"].value is None
    assert w["标的表"]["F2"].value is None
    assert w["标的表"]["G2"].value == "定制"
    assert w["标的表"]["D2"].data_type == "s"
    assert w["标的表"]["H2"].value == 254
    assert w["标的表"]["I2"].value == .09 and w["标的表"]["J2"].value == 23.62
    assert w["标的表"]["I2"].number_format == "0.00"
    assert w["投标人表"]["E4"].value == "其他"
    assert w["投标人表"]["F4"].value == "未通过，资质不符"
    assert w["投标人表"]["E1"].fill.fgColor.rgb.endswith("1F4E79")
    assert w["标的表"]["L1"].fill.fgColor.rgb.endswith("A6A6A6")
    assert original == snapshot
    path=tmp_path/'gold.xlsx'; path.write_bytes(data)
    records=read_gold_workbook(path)
    assert not records["标的表"][0]["cells"]["品牌"]["inferred"]


def test_empty_cob_package_and_no_winner_still_export():
    rows=official_rows([{"announcement_id": "aid", "packages": [{"package_no": "1", "package_kind": "unpartitioned", "cobs": [], "subs": []}]}])
    assert rows["总表"][0][3] == "未分包" and len(rows["总表"]) == 1
    assert rows["标的表"] == []


@pytest.mark.parametrize("gold, out, expected", [
    ({"value":None,"inferred":False},"多抽",False), ({"value":None,"inferred":False},None,True),
    ({"value":"值","inferred":True},None,True), ({"value":"值","inferred":True},"错",False),
    ({"value":"值","inferred":False},None,False)])
def test_gold_cell_policy(gold,out,expected):
    assert score_gold_cell(gold,out) is expected


def test_storage_adapter_uses_source_names_and_explicit_db_mapping():
    from server.results import result_from_storage, annotate_units
    result=example_result(); raw={"official_result":result,"project_map":{"db-p":"1"}}
    def query(sql, params):
        return [{"project_id":"db-p","package_no":"1","raw_json":raw}]
    rows=annotate_units([{"project_id":"db-p","package_no":"1"}],query)
    assert rows[0]["package_label"] == "一标段"
    assert result_from_storage(result["announcement_id"],raw,query) == result


def test_same_number_database_units_do_not_become_rounds_of_one_package():
    from db.load_runs import _package_base_key
    original={"project_name":"采购","source_project_no":"PROJECT","package_no":"1","source_package_no":"1"}
    assert _package_base_key(original | {"package_kind":"lot"}) != _package_base_key(original | {"package_kind":"contract_package"})
    assert _package_base_key(original) == "PROJECT|1"
    assert _package_base_key(original | {"package_kind":"lot", "package_no":"lot:1"}) == _package_base_key(
        original | {"package_kind":"lot", "package_no":"unit:2"})


def test_invalid_review_enum_is_audited_and_not_business_value():
    row=normalized(observation('s1',{'supplier_name':'甲','review_status':'未通过'}))[0]
    assert row.fields['review_status'].status=='unparsable'
    assert 'invalid_review_status' in row.validation_errors
    assert _value(row,'review_status') is None


def test_white_fill_is_not_inference_but_colored_data_fill_is(tmp_path):
    from openpyxl.styles import PatternFill
    w=load_workbook(BytesIO(export_workbook([example_result()])))
    w['标的表']['F2'].fill=PatternFill('solid',fgColor='FFFFFF')
    w['标的表']['G2'].fill=PatternFill('solid',fgColor='FFFF00')
    path=tmp_path/'gold.xlsx'; w.save(path)
    cells=read_gold_workbook(path)['标的表'][0]['cells']
    assert not cells['品牌']['inferred'] and cells['规格型号']['inferred']


def test_full_pipeline_with_fixed_model_preserves_new_fields_and_custom_spec(tmp_path,monkeypatch):
    from ict.pipeline import run_announcement
    from ict.llm import LLMResult
    html='''<html><body><div class="vF_detail_content"><p>一、采购结果</p><p>甲最终报价90元；乙未通过，资质不符。</p>
    <table><tr><th>名称</th><th>规格型号</th></tr><tr><td>设备</td><td>定制</td></tr></table></div></body></html>'''
    (tmp_path/'notice_with_underscores.html').write_text(html,encoding='utf-8')
    class Model:
        def complete(self, *, step, prompt_version, user, **kwargs):
            payload=json.loads(user)
            if step == 'read_announcement_summary':
                answer = {}
            elif step == 'read_announcement_body':
                answer = {'project_name': '采购', 'package_mode': 'single', 'packages': [
                    {'source_package_no': '1', 'package_kind': 'unpartitioned', 'package_name': '未分包', 'candidates': [
                    {'entity_type': 'cob', 'source_ref': 'html:body', 'source_class': 'cob_detail', 'fields':
                        {'object_name':'设备','spec_model':'定制','product_supplier':'厂家','quantity':1,'unit_price':90,'total_price':90}},
                    {'entity_type': 'sub', 'source_ref':'html:body','fields':{'supplier_name':'甲','is_winner':True,'bid_amount':'100元'}},
                    {'entity_type': 'sub', 'source_ref':'html:body','fields':{'supplier_name':'甲','is_winner':True,'bid_amount':'90元'}},
                    {'entity_type': 'sub', 'source_ref':'html:body','fields':{'supplier_name':'乙','review_status':'failed','review_failure_reason':'资质不符','score':91.02}}]}]}
            elif step=='merge_subs':
                ids=[c['candidate_id'] for c in payload['candidates']]
                answer={"groups":[{"candidate_ids":ids[:2],"name_from":ids[0],"bid_amount_from":ids[1],"winner":True,"reason":"最终报价"},
                    {"candidate_ids":ids[2:],"name_from":ids[2],"score_from":ids[2],"review_from":ids[2],"review_reason_from":ids[2],"winner":False,"reason":"未通过"}]}
            elif step=='merge_candidates':
                answer={"deltas":[]}
            else:
                raise AssertionError('unexpected step '+step)
            return LLMResult(json.dumps(answer), 'fixed','fixed',prompt_version,0)
    monkeypatch.setattr('ict.pipeline.load_index',lambda *args:None)
    report=run_announcement('notice_with_underscores',llm=Model(),html_dir=tmp_path,runs_root=tmp_path/'runs')
    p=report.result['packages'][0]
    assert p['package_kind']=='unpartitioned'
    assert p['cobs'][0]['spec_model']=='定制' and p['cobs'][0]['brand'] is None
    assert p['subs'][0]['bid_amount']==90 and p['subs'][1]['review_failure_reason']=='资质不符'
    assert p['subs'][1]['score']==91.02
    assert report.result['announcement_id']=='notice_with_underscores'
