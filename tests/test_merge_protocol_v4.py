"""Cross-operation regressions: grouping, accounting, source ownership and final bindings."""
import json

import pytest

from ict.catalog import Catalog
from ict.llm import LLMResult
from ict.schemas import MergedProjects, Project, Cob, Sub
from ict.steps.s07_normalize import normalize_candidates
from ict.steps.s08b_repair import repair_packages
from ict.steps.s09_supplier import complete_suppliers
from test_rules import _cob, _merge


class Model:
    def __init__(self, deltas=(), accounting=(), decisions=()):
        self.plan = {"deltas": list(deltas), "quantity_accounting": list(accounting)}
        self.decisions = decisions
        self.requests = []

    def complete(self, *, step, prompt_version, user, thinking=False):
        self.requests.append((step, user))
        response = {"decisions": self.decisions} if step == "complete_suppliers" else self.plan
        return LLMResult(json.dumps(response, ensure_ascii=False), "fake", "fixed", prompt_version, 0)


def terminal_rows():
    quantities = [(2, "个"), (1, "台"), (2, "台"), (1, "台"), (1, "个"),
                  (3, "个"), (1, "台"), (2, "台"), (2, "台"), (1, "台")]
    return [_cob(f"c{index}", "html", 100, {"object_name": "终端管理器", "quantity": number,
                 "unit": unit, "unit_price": 2000, "brand": "大华", "spec_model": "DH-ITSE0400-TA -N02"})
            for index, (number, unit) in enumerate(quantities)]


def terminal_plan():
    tai = ["c1", "c2", "c3", "c6", "c7", "c8", "c9"]
    ge = ["c0", "c4", "c5"]
    return [
        {"op": "split", "group_id": "g:0", "parts": [{"as": "g:tai", "members": tai},
                                                     {"as": "g:ge", "members": ge}]},
        {"op": "sum", "group_id": "g:tai", "sources": tai, "as": "r:tai"},
        {"op": "sum", "group_id": "g:ge", "sources": ge, "as": "r:ge"},
        {"op": "price_from", "group_id": "g:tai", "source": "r:tai"},
        {"op": "price_from", "group_id": "g:ge", "source": "r:ge"},
    ]


def test_two_named_sums_survive_package_amount_and_late_repair():
    merged = _merge(terminal_rows(), amount=6000, llm=Model(terminal_plan()))
    assert sorted((row.unit, row.quantity, row.total_price) for row in merged.projects[0].cobs) == [
        ("个", 6, 12000), ("台", 10, 20000)]
    assert not merged.unresolved
    before = [row.model_dump() for row in merged.projects[0].cobs]
    repair_packages(merged, {}, Model())
    assert [row.model_dump() for row in merged.projects[0].cobs] == before
    assert not merged.alternatives


def test_a_consumed_group_cannot_alias_to_the_first_child():
    plan = terminal_plan()
    plan[1]["group_id"] = "g:0"
    merged = _merge(terminal_rows(), llm=Model(plan))
    assert any(item.get("transaction_rolled_back") for item in merged.merge_decisions)
    assert any(item["reason"] == "merge_model_fallback" for item in merged.unresolved)


def test_unexplained_observations_are_partial_instead_of_silent_loss():
    merged = _merge(terminal_rows(), llm=Model())
    assert merged.status == "partial"
    assert len([item for item in merged.unresolved if item["reason"] == "quantity_observation_unresolved"]) == 9


def test_cross_file_duplicates_can_be_accounted_without_summing():
    first = _cob("one", "html", 100, {"object_name": "屏幕", "quantity": 2, "unit": "台", "unit_price": 100})
    second = _cob("two", "pdf", 90, {"object_name": "屏幕", "quantity": 2, "unit": "台", "unit_price": 100}, source_class="award_detail")
    model = Model([{"op": "price_from", "group_id": 0, "source": "c:one"}],
                  [{"candidate_id": "two", "disposition": "duplicate", "relates_to": "c:one", "reason": "同一成交行"}])
    merged = _merge([first, second], llm=model)
    assert merged.projects[0].cobs[0].quantity == 2
    assert not merged.unresolved


def test_repeated_candidate_leaf_in_nested_sums_is_rejected():
    rows = terminal_rows()[:2]
    rows[1].fields["unit"].raw_value = "个"
    plan = [{"op": "sum", "group_id": 0, "sources": ["c0", "c1"], "as": "r:sub"},
            {"op": "sum", "group_id": 0, "sources": ["r:sub", "c0"], "as": "r:total"}]
    merged = _merge(rows, llm=Model(plan))
    assert any("重复使用" in item.get("reject", "") for item in merged.merge_decisions)


def test_nameless_html_can_take_a_tender_name_without_budget_or_quantity():
    html = _cob("html", "html", 100, {"quantity": 1, "unit": "项", "total_price": 1638756})
    tender = _cob("tender", "pdf", 20, {"object_name": "互联网接入服务", "quantity": 99, "unit": "条",
                                       "total_price": 1639600}, source_class="tender_requirement")
    model = Model([{"op": "name_from", "group_id": 1, "candidate_id": "tender"}])
    merged = _merge([html, tender], llm=model)
    assert len(merged.projects[0].cobs) == 1
    row = merged.projects[0].cobs[0]
    assert (row.object_name, row.quantity, row.unit, row.total_price) == ("互联网接入服务", 1, "项", 1638756)
    assert html.fields["object_name"].raw_value is None
    assert not merged.unresolved


def test_multiple_nameless_candidates_are_distinct_groups():
    html = [_cob("a", "html", 100, {"total_price": 100}), _cob("b", "html", 100, {"total_price": 200})]
    merged = _merge(html, llm=Model())
    assert not merged.projects[0].cobs
    assert len([item for item in merged.unresolved if item["reason"] == "missing_object_name"]) == 2


def test_package_amount_supplies_only_total_without_clearing_quantity():
    row = _cob("service", "html", 100, {"object_name": "整体服务", "quantity": 1, "unit": "项"})
    merged = _merge([row], amount=100, llm=Model([{"op": "price_from", "group_id": 0, "source": "p:amount"}]))
    cob = merged.projects[0].cobs[0]
    assert (cob.quantity, cob.unit, cob.total_price) == (1, "项", 100)
    assert not merged.unresolved


def test_rejecting_a_calculation_operand_invalidates_its_derived_total():
    row = _cob("service", "html", 100, {"object_name": "整体服务", "quantity": 3, "unit_price": 100})
    merged = _merge([row], llm=Model([{"op": "fields_reject", "target": "c:service", "keys": ["quantity"]}]))
    assert merged.projects[0].cobs[0].quantity is None
    assert merged.projects[0].cobs[0].total_price is None


def test_excluded_bandwidth_does_not_leak_into_the_overall_service():
    overall = _cob("main", "html", 100, {"object_name": "卫生专网主链路", "total_price": 100})
    point = _cob("point", "pdf", 20, {"object_name": "链路点位", "spec_model": "1G", "quantity": 1},
                 source_class="tender_requirement")
    merged = _merge([overall, point], llm=Model([{"op": "exclude", "candidate_ids": ["point"], "kind": "not_an_object"}]))
    assert merged.projects[0].cobs[0].spec_model is None
    assert merged.projects[0].cobs[0].quantity is None


def test_explicit_null_removes_an_existing_service_description():
    row = _cob("service", "html", 100, {"object_name": "运维服务", "spec_model": "服务范围：详见；服务时间：一年"})
    merged = _merge([row], llm=Model([{"op": "fields_reject", "target": "g:0", "keys": ["spec_model"]}]))
    assert merged.projects[0].cobs[0].spec_model is None
    assert row.fields["spec_model"].raw_value == "服务范围：详见；服务时间：一年"
    assert any(item["field"] == "spec_model" and item["method"] == "rejected" for item in merged.field_bindings)


@pytest.mark.parametrize("name", ["C17010200网络接入服务", "C17010200 网络接入服务"])
def test_catalog_separates_name_and_code_without_overwriting_raw_text(name):
    row = _cob("net", "html", 100, {"object_name": "专网", "category_name": name})
    result = normalize_candidates("test", [row], Catalog())
    assert row.fields["category_name"].raw_value == name
    assert row.fields["category_name"].normalized_value == "网络接入服务"
    assert row.fields["category_code"].normalized_value == "C17010200"
    assert not result.failures


def inference_fixture():
    return MergedProjects(run_id="test", status="success", projects=[Project(
        project_id="保险|1", project_name="保险", package_no="1",
        cobs=[Cob(object_name="团体意外伤害保险"), Cob(object_name="团体意外伤害保险")],
        subs=[Sub(supplier_name="保险公司", is_winner=True)])])


def test_post_merge_inference_addresses_same_name_objects_separately():
    merged = inference_fixture()
    model = Model(decisions=[{"cob_ref": "保险|1:cob:0", "decision": "confirm", "winner_ref": "winner:0", "reason": "直接保险服务"},
                             {"cob_ref": "保险|1:cob:1", "decision": "uncertain", "reason": "证据不足"}])
    assert not complete_suppliers(merged, model)
    assert [cob.product_supplier for cob in merged.projects[0].cobs] == ["保险公司", None]
    assert merged.projects[0].subs[0].cooperative_product_suppliers == ["保险公司"]
    assert merged.projects[0].cobs[0].brand is None


def test_unknown_winner_reference_never_fills_a_supplier():
    merged = inference_fixture()
    model = Model(decisions=[{"cob_ref": f"保险|1:cob:{index}", "decision": "confirm", "winner_ref": "winner:99", "reason": "猜测"}
                             for index in range(2)])
    assert complete_suppliers(merged, model)
    assert all(cob.product_supplier is None for cob in merged.projects[0].cobs)


def test_forbidden_inference_and_existing_brand_skip_the_supplier_model():
    merged = inference_fixture()
    merged.field_bindings = [{"package_no": "1", "cob_index": 0, "field": "product_supplier", "forbid_inference": True}]
    merged.projects[0].cobs[1].brand = "已有品牌"
    model = Model()
    assert not complete_suppliers(merged, model)
    assert not model.requests
