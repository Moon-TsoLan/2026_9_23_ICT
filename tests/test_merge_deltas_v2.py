"""Step 8 v2: rules mark instead of deleting, deltas can move fields, and `accepted` is not `applied`.

Everything here lives on the path where the model judges content, so each test runs step 8 with a
fixed model answer. The deterministic no-model path keeps the old semantics and stays in
test_rules.py.
"""
import json

from ict.catalog import Catalog
from ict.llm import LLMResult
from ict.steps.s07_normalize import normalize_candidates
from test_rules import _cob, _merge, _merged_with_deltas


class _Asker:
    """Stand-in merge model: records what it was shown, answers with a fixed delta list."""

    def __init__(self, deltas=()):
        self.deltas = list(deltas)
        self.payload = None
        self.thinking = None

    def complete(self, *, step, prompt_version, user, thinking=False):
        self.payload = json.loads(user)
        self.thinking = thinking
        return LLMResult(json.dumps({"deltas": self.deltas}, ensure_ascii=False),
                         "asker", "test", prompt_version, 1)


def _rows(merged):
    return [row for project in merged.projects for row in project.cobs]


def test_step7_marks_a_non_numeric_cell_instead_of_blanking_it_in_silence():
    quote = _cob("cand_000001", "pdf", 90, {"object_name": "在线课程资源库建设",
                                            "unit_price": "{=响应报价/数量}元",
                                            "total_price": "「汇总引用」 元"}, source_class="bid_quote")
    marked = normalize_candidates("run_x", [quote], Catalog()).candidates[0]
    assert marked.fields["unit_price"].status == "unparsable"
    assert marked.fields["unit_price"].raw_value == "{=响应报价/数量}元"
    assert marked.fields["unit_price"].normalized_value is None
    assert "invalid_number" in marked.validation_errors


def test_a_group_with_no_number_still_keeps_the_quantity_and_unit_it_stated():
    """t20260603_26682468 package 1: four of five rows used to come out with neither.

    A template quote sheet states 1.0000 and 项 but leaves the money as a formula placeholder. That
    is not a price reading, yet the row's own observed quantity and unit are not invented, and
    dropping them made the package look like the attachment had said nothing.
    """
    html = _cob("cand_000001", "html", 100, {"object_name": "在线课程资源库建设",
                                             "category_name": "高等教育服务"})
    quote = _cob("cand_000002", "pdf", 90, {"object_name": "在线课程资源库建设", "quantity": "1.0000",
                                            "unit": "项", "unit_price": "{=响应报价/数量}元"},
                 source_class="bid_quote")
    merged = _merge([html, quote], amount=797737, llm=_Asker())
    row = _rows(merged)[0]
    assert row.quantity == 1.0
    assert row.unit == "项"
    assert row.unit_price is None
    assert row.total_price == 797737


def test_price_from_naming_a_row_without_a_number_is_refused_out_loud():
    html = _cob("cand_000001", "html", 100, {"object_name": "在线课程资源库建设"})
    quote = _cob("cand_000002", "pdf", 90, {"object_name": "在线课程资源库建设", "quantity": "1.0000",
                                            "unit": "项", "unit_price": "{=响应报价/数量}元"},
                 source_class="bid_quote")
    merged = _merged_with_deltas([html, quote], [{
        "op": "price_from", "group_id": 0, "candidate_id": "cand_000002",
        "reason": "报价明细行给出数量与单位", "confidence": 0.8}])
    entry = next(item for item in merged.merge_decisions if item.get("op") == "price_from")
    assert entry["accepted"] is False
    assert entry["reject"] == "target_states_no_number_price"
    assert _rows(merged)[0].quantity == 1.0


def test_price_from_can_take_only_the_fields_it_names():
    """Whole-bundle replacement stays the default; keys is the escape hatch for quantity and unit.

    The rule against mixing sources is about money: a unit price from one row joined to a quantity
    from another invents a total nobody wrote. Taking the stated count from a different row is an
    observation, and it is now expressible without pretending it is a price reading.
    """
    html = _cob("cand_000001", "html", 100, {"object_name": "服务器", "unit_price": 100, "quantity": 1,
                                             "unit": "台", "total_price": 100})
    att = _cob("cand_000002", "pdf", 90, {"object_name": "服务器", "quantity": 3, "unit": "台"},
               source_class="bid_quote")
    merged = _merged_with_deltas([html, att], [{
        "op": "price_from", "group_id": 0, "candidate_id": "cand_000002",
        "keys": ["quantity", "unit"], "reason": "附件写了三台", "confidence": 0.7}])
    row = _rows(merged)[0]
    assert row.quantity == 3 and row.unit == "台"
    assert row.unit_price == 100 and row.total_price == 100
    entry = next(item for item in merged.merge_decisions if item.get("op") == "price_from")
    assert entry["accepted"] is True and entry["applied"] is True
    assert entry["bundle"] == "partial_keys"


def test_excluding_a_row_hands_what_it_stated_to_the_group_it_came_from():
    """The exclusion that used to delete the announcement's only category statement.

    t20260805_27080785: one HTML row carried 品目 and 品牌 and pointed its details into the
    attachment. Excluding it left the surviving rows with no category at all, because a dropped row
    donated nothing to anybody.
    """
    index_row = _cob("cand_000001", "html", 100, {"object_name": "智慧物联集成",
                                                  "category_name": "其他组合音像设备",
                                                  "category_code": "A02091399", "brand": "海康威视"})
    detail = _cob("cand_000002", "pdf", 90, {"object_name": "智慧物联集成", "unit_price": 898890,
                                             "quantity": 1, "unit": "项", "total_price": 898890},
                  source_class="bid_quote")
    merged = _merged_with_deltas([index_row, detail], [{
        "op": "exclude", "candidate_id": "cand_000001", "kind": "index_row",
        "reason": "公告只说明标的存在，内容在附件", "confidence": 0.8}])
    row = _rows(merged)[0]
    assert row.category_code == "A02091399"
    assert row.brand == "海康威视"
    assert row.total_price == 898890
    entry = next(item for item in merged.merge_decisions if item.get("op") == "exclude")
    assert "category_name" in entry["donated_keys"]


def test_fields_from_moves_a_category_onto_a_group_that_never_stated_it():
    """Cross-group field movement, which no delta could express before.

    The announcement states a category once, in a row the model excludes; the objects that need it
    are named differently, in another group. The rule side refuses to guess which object a shared
    brand belongs to, so the model has to name the row and the field.
    """
    named = _cob("cand_000001", "html", 100, {"object_name": "电子班牌显示屏WS-B22GS",
                                              "category_name": "其他显示设备"})
    other = _cob("cand_000002", "pdf", 90, {"object_name": "智能门禁显示屏", "spec_model": "DS-K1T770M"},
                 source_class="qualification")
    merged = _merged_with_deltas([named, other], [{
        "op": "fields_from", "group_id": 1, "candidate_id": "cand_000001",
        "keys": ["category_name"], "reason": "整包同一品目", "confidence": 0.6}])
    by_name = {row.object_name: row for row in _rows(merged)}
    assert by_name["智能门禁显示屏"].category_name == "其他显示设备"
    assert by_name["智能门禁显示屏"].spec_model == "DS-K1T770M"
    entry = next(item for item in merged.merge_decisions if item.get("op") == "fields_from")
    assert entry["applied"] is True and entry["filled"] == ["category_name"]


def test_a_donor_fills_only_blanks_and_never_overwrites_a_stated_value():
    poor = _cob("cand_000001", "html", 100, {"object_name": "服务器", "brand": "浪潮"})
    rich = _cob("cand_000002", "pdf", 90, {"object_name": "机柜", "brand": "华为",
                                           "category_name": "其他机柜"}, source_class="bid_quote")
    merged = _merged_with_deltas([poor, rich], [{
        "op": "fields_from", "group_id": 0, "candidate_id": "cand_000002",
        "keys": ["brand", "category_name"], "reason": "补品目", "confidence": 0.5}])
    by_name = {row.object_name: row for row in _rows(merged)}
    assert by_name["服务器"].brand == "浪潮"
    assert by_name["服务器"].category_name == "其他机柜"


def test_suspect_rows_are_marked_and_shown_instead_of_being_deleted_before_the_model():
    """The rules no longer get the last word on whether a row is an object.

    With a model in the loop the pointer row stays - marked, and visible in the payload - so the
    judgement is auditable as a model decision. Without a model the deterministic baseline has to
    stand on its own and keeps deleting it, which is what the offline tests and replays rely on.
    """
    def pair():
        return [_cob("cand_000001", "html", 100, {"object_name": "实训设备一批"},
                     issues=["line_fields_point_to_attachment"]),
                _cob("cand_000002", "pdf", 90, {"object_name": "数控车床", "unit_price": 1000,
                                                "quantity": 2, "unit": "台", "total_price": 2000},
                     source_class="bid_quote")]

    asker = _Asker()
    merged = _merge(pair(), amount=2000, llm=asker)
    shown = {row["candidate_id"]: row for row in asker.payload["candidates"]}
    assert shown["cand_000001"]["rule_suspect"] == ["suspect_html_pointer_row"]
    assert shown["cand_000002"]["price_reading"] is True
    assert asker.thinking is True
    names = {row.object_name for row in _rows(merged)}
    assert names == {"实训设备一批", "数控车床"}

    without_model = _merge(pair(), amount=2000, llm=None)
    assert {row.object_name for row in _rows(without_model)} == {"数控车床"}


def test_a_placeholder_price_is_shown_as_one_and_not_offered_as_a_source():
    quote = _cob("cand_000001", "pdf", 90, {"object_name": "在线课程资源库建设", "quantity": "1.0000",
                                            "unit": "项", "unit_price": "{=响应报价/数量}元"},
                 source_class="bid_quote")
    html = _cob("cand_000002", "html", 100, {"object_name": "别的东西", "unit_price": 10,
                                             "quantity": 1, "unit": "个", "total_price": 10})
    asker = _Asker()
    _merge([html, quote], amount=10, llm=asker)
    shown = {row["candidate_id"]: row for row in asker.payload["candidates"]}
    assert "unit_price" not in shown["cand_000001"]
    assert shown["cand_000001"]["price_reading"] is False
    assert shown["cand_000001"]["unparsable_fields"] == ["unit_price"]