"""Offline regressions for the 2026-10-12 handoff contracts; no live clients."""
import json
from pathlib import Path

import pytest

from ict.candidates import seal_candidate
from ict.llm import complete_json, FakeLLMClient, LLMValidationError, ValidationIssue
from ict.receiving import ExtractionReceiver
from ict.schemas import Amount, PackageAmountObservation, HtmlTables, CandidateFile, MergeEvidence
from ict.bindings import bind_packages
from ict.steps.s08_merge import _value
from ict.sub_merge import merge_subs
from test_merge_protocol_v4 import Model
from test_rules import _cob, _merge, _understanding
from test_binding_evidence import fixture, Binder


def test_source_total_rejection_preserves_operands_and_computes_with_dependencies():
    row = _cob("a", "pdf", 100, {"object_name": "设备", "unit_price": 7680,
                                    "quantity": 10, "unit": "组", "total_price": 800})
    model = Model([{"op": "fields_reject", "target": "c:a", "keys": ["total_price"]}],
                  [{"candidate_id": "a", "keys": ["total_price"], "disposition": "alternative",
                    "relates_to": "c:a", "reason": "总价读数不可靠"}])
    merged = _merge([row], llm=model)
    cob = merged.projects[0].cobs[0]
    assert (cob.unit_price, cob.quantity, cob.total_price) == (7680, 10, 76800)
    audit = next(item for item in merged.field_bindings if item["field"] == "total_price")
    assert audit["method"] == "calculated" and audit["calculation"]["formula"] == "unit_price*quantity"
    assert {(item["candidate_id"], item["field"]) for item in audit["dependencies"]} == {("a", "unit_price"), ("a", "quantity")}
    assert len(model.requests) == 1 and row.fields["total_price"].raw_value == 800


def test_final_total_rejection_forbids_calculation():
    row = _cob("a", "html", 100, {"object_name": "设备", "unit_price": 7680, "quantity": 10})
    merged = _merge([row], llm=Model([{"op": "fields_reject", "target": "g:0", "keys": ["total_price"]}]))
    assert merged.projects[0].cobs[0].total_price is None
    binding = next(item for item in merged.field_bindings if item["field"] == "total_price")
    assert binding["forbid_inference"] and binding["method"] == "rejected"


def test_rejecting_final_quantity_invalidates_a_default_total_derived_from_it():
    row = _cob("a", "html", 100, {"object_name": "设备", "unit_price": 10, "quantity": 2})
    merged = _merge([row], llm=Model([{"op": "fields_reject", "target": "g:0", "keys": ["quantity"]}]))
    assert merged.projects[0].cobs[0].quantity is None
    assert merged.projects[0].cobs[0].total_price is None


def test_excluded_measurement_cannot_be_counted_in_two_final_objects():
    rows = [_cob("a", "html", 100, {"object_name": "甲"}), _cob("b", "html", 100, {"object_name": "乙"}),
            _cob("shared", "html", 90, {"object_name": "汇总", "quantity": 1, "total_price": 100})]
    plan = [{"op": "exclude", "candidate_id": "shared", "kind": "subtotal_row"},
            {"op": "price_from", "group_id": 0, "source": "c:shared", "keys": ["quantity", "total_price"]},
            {"op": "price_from", "group_id": 1, "source": "c:shared", "keys": ["quantity", "total_price"]}]
    merged = _merge(rows, llm=Model(plan))
    assert any("多个最终组" in item.get("detail", "") for item in merged.repairs)


def test_partial_binding_does_not_adopt_an_empty_default_unit():
    rows = [_cob("old", "html", 100, {"object_name": "设备", "quantity": 1, "unit_price": 400000, "total_price": 400000}),
            _cob("new", "pdf", 90, {"object_name": "设备", "quantity": 1, "unit_price": 400000, "total_price": 400000})]
    model = Model([{"op": "price_from", "group_id": 0, "source": "c:new", "keys": ["unit_price", "quantity", "total_price"]}],
                  [{"candidate_id": "old", "keys": ["quantity", "total_price"], "disposition": "duplicate", "reason": "重复观测"}])
    merged = _merge(rows, llm=model)
    assert not merged.unresolved and len(model.requests) == 1
    unit = next(item for item in merged.field_bindings if item["field"] == "unit")
    assert unit["value"] is None and not unit["dependencies"]
    assert unit["source"] is None and not unit["source_candidate_ids"]


def test_accounting_overlap_is_business_uncertainty_not_a_schema_retry():
    row = _cob("a", "html", 100, {"object_name": "设备", "quantity": 1, "total_price": 100})
    model = Model(accounting=[{"candidate_id": "a", "keys": ["quantity"], "disposition": "alternative", "reason": "有争议"}])
    merged = _merge([row], llm=model)
    assert any(item["reason"] == "accounting_conflicts_with_binding" for item in merged.unresolved)
    assert not any(item["reason"] == "merge_model_fallback" for item in merged.unresolved)
    assert len(model.requests) == 1


def test_same_candidate_can_explain_distinct_unused_fields_separately():
    rows = [_cob("a", "html", 100, {"object_name": "设备", "quantity": 1, "total_price": 100}),
            _cob("b", "pdf", 90, {"object_name": "设备", "quantity": 1, "total_price": 100})]
    model = Model(accounting=[{"candidate_id": "b", "keys": [key], "disposition": "duplicate", "reason": "重复"}
                              for key in ("quantity", "total_price")])
    merged = _merge(rows, llm=model)
    assert not merged.unresolved and len(model.requests) == 1


def test_unavailable_observation_is_not_retried_as_malformed_json():
    row = _cob("a", "html", 100, {"object_name": "设备"})
    model = Model([{"op": "price_from", "group_id": 0, "source": "c:a"}])
    merged = _merge([row], llm=model)
    assert len(model.requests) == 1
    assert merged.repairs[0]["category"] == "business"


def contexts():
    return [{"file_id": "a", "page_no": 3, "source_segments": [
        {"source_ref": "file:a:native:p3", "file_id": "a", "page_no": 3, "text": "包1服务，项目预算"}]}]


def test_invalid_amount_does_not_erase_independent_names_and_need_links_on_retry():
    first = {"candidates": [{"entity_type": "cob", "source_ref": "file:a:native:p3", "fields": {"object_name": "服务"}},
                             {"entity_type": [], "source_ref": "file:a:native:p3", "fields": {}}],
             "package_amounts": [{"package_no": None, "raw_text": "741.32万元", "amount_kind": "budget", "source_ref": "file:a:native:p3"},
                                 {"package_no": "1", "raw_text": "1元", "amount_kind": [], "source_ref": "file:a:native:p3"}],
             "need_resolutions": [{"need_id": "n", "field": "object_name", "target_candidate_id": "html", "source_ref": "file:a:native:p3"}]}
    model = FakeLLMClient({"extract": [json.dumps(first), '{"candidates": [], "package_amounts": []}']})
    result = complete_json(model, step="extract", prompt_version="unused", user="{}", validate=lambda p: "",
                           receiver=ExtractionReceiver(contexts()))
    assert len(result["candidates"]) == len(result["need_resolutions"]) == 1
    assert not result["package_amounts"]
    rejected = result["reception"]["rejected_items"]
    assert len(rejected) == 3 and rejected[1]["raw"]["package_no"] is None


def test_repair_has_previous_response_and_does_not_silently_splice_delta_plans():
    model = Model([{"op": "exclude", "candidate_ids": ["absent"], "kind": "index_row"}])
    _merge([_cob("a", "html", 100, {"object_name": "设备"})], llm=model)
    assert len(model.requests) == 2
    assert 'previous_response' in model.requests[1][1] and 'plan_constraint' in model.requests[1][1]


def test_validator_implementation_error_does_not_blame_or_retry_the_model():
    model = FakeLLMClient({"step": ["{}", "{}"]})
    def invalid_validator(data):
        raise KeyError("obsolete_contract")
    with pytest.raises(LLMValidationError) as caught:
        complete_json(model, step="step", prompt_version="unused", user="{}", validate=invalid_validator)
    assert caught.value.issues[0].category == "implementation" and model.used["step"] == 1


def test_amount_kind_never_defaults_to_award_and_legacy_sub_amount_is_retained():
    assert Amount(scope="announcement").amount_kind == "unknown"
    assert PackageAmountObservation(package_no="1", raw_text="100").amount_kind == "unknown"
    row = seal_candidate(candidate_id="sub", entity_type="sub", project_id="p", package_no="1",
                         source_type="html", file_id=None, source_priority=100,
                         raw_fields={"supplier_name": "企业", "total_price": "189600元", "supplier_award_amount": "100元"})
    assert row.unmapped_fields["total_price"] == "189600元"
    from ict.steps.s07_normalize import normalize_candidates
    from ict.catalog import Catalog
    normalize_candidates("x", [row], Catalog())
    assert row.fields["supplier_award_amount"].normalized_value == 100


def test_attachment_only_amounts_reach_binding_and_unresolved_is_not_adopted(tmp_path):
    notice, understanding = fixture(tmp_path)
    amounts = [PackageAmountObservation(package_no="2", raw_text="200元", amount_yuan=200, amount_kind="award", source_ref="html:t0:r0")]
    answer = {"bindings": [], "amount_bindings": [
        {"package_no": "1", "amount_ref": "amount:attachment:0", "amount_kind": "award", "reason": "包1证据"},
        {"package_no": "2", "amount_ref": None, "reason": "未决"}],
        "observation_bindings": [{"amount_ref": "amount:attachment:0", "package_no": "1", "amount_kind": "award", "reason": "包1证据"}]}
    invalid = json.loads(json.dumps(answer))
    invalid["observation_bindings"][0]["amount_ref"] = []
    model = FakeLLMClient({"bind_packages": [json.dumps(invalid), json.dumps(answer)]})
    audit, errors = bind_packages(understanding, [], notice, HtmlTables(run_id="x", status="success"), model, package_amounts=amounts)
    assert not errors and amounts[0].package_no == "1" and audit["observation_bindings"]
    assert understanding.packages[0].package_amount.amount_yuan == 200
    assert model.used["bind_packages"] == 2


def test_native_evidence_survives_an_independent_page_selection(tmp_path):
    from ict.pipeline import _merge_evidence
    from ict.html_context import parse_notice
    path = tmp_path / "a.html"
    path.write_text("<html><body></body></html>", encoding="utf-8")
    from ict.schemas import PageDecisions, PageDecision
    selected = PageDecisions(run_id="x", status="success", page_decisions=[PageDecision(file_id="a", page_no=37,
        relevance=1, package_scope="1", extraction_mode="text", reason="另页")])
    pages = {"a": [{"page_no": 3, "text": "已发现名称", "tables": [], "source_segments": contexts()[0]["source_segments"]}]}
    evidence = _merge_evidence(parse_notice(path), None, _understanding(), None, None, selected, pages, [], {})
    assert evidence.attachment_pages["a:3"]["native_segments"]
    assert not evidence.attachment_pages["a:3"]["selected_for_deep_extraction"]


def test_sub_identity_merge_uses_sources_and_keeps_joint_venture_whole():
    rows = [seal_candidate(candidate_id=cid, entity_type="sub", project_id="p", package_no="1", source_type="html",
        file_id=None, source_priority=100, raw_fields={"supplier_name": name, "is_winner": win})
        for cid, name, win in [("a", "企业(联合体成员：院)", True), ("b", "企业，联合体成员：院", False)]]
    answer = {"groups": [{"candidate_ids": ["a", "b"], "name_from": "a", "score_from": None,
                          "amount_from": None, "winner": True, "reason": "同一联合体"}], "excluded": []}
    result = merge_subs(rows, "1", FakeLLMClient({"merge_subs": json.dumps(answer)}), None, _value)
    assert result["accepted"] and len(result["subs"]) == 1
    assert result["subs"][0].supplier_name == "企业(联合体成员：院)"


def test_explicit_sub_winner_decision_cannot_be_revived_by_old_award_hints():
    from ict.schemas import MergedProjects, Project, Sub, Cob
    from ict.steps.s08b_repair import repair_packages
    merged = MergedProjects(run_id="x", status="partial", projects=[Project(project_id="p", package_no="1",
        project_name="项目", cobs=[Cob(object_name="设备")], subs=[Sub(supplier_name="推荐候选人", is_winner=False)])],
        sub_merge_decisions=[{"package_no": "1", "accepted": True}])
    repair_packages(merged, {"1": ["推荐候选人"]}, None)
    assert not merged.projects[0].subs[0].is_winner


def test_sniffed_docx_works_with_doc_suffix(tmp_path):
    from docx import Document
    from ict.documents import read_native
    path = tmp_path / "wrong.doc"
    document = Document()
    document.add_paragraph("原生正文")
    document.save(path)
    assert "原生正文" in read_native(path, "docx")[0]["text"]


def test_sniffed_xlsx_works_with_a_wrong_suffix(tmp_path):
    from openpyxl import Workbook
    from ict.documents import read_native
    path = tmp_path / "wrong.xls"
    book = Workbook()
    book.active.append(["标的", "金额"])
    book.active.append(["设备", 100])
    book.save(path)
    assert "设备" in read_native(path, "xlsx")[0]["text"]
