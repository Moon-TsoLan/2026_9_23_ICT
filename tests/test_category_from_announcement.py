# -*- coding: utf-8 -*-
"""品目只写在公告概要里的公告：模型判归属，编码与名称由 2022 品目目录补。

全量 1038 则里 375 则的品目只出现在公告概要那一行，正文表格与附件都没有这一列。第 1 步把
它抄成 announcement_categories，第 8 步让模型把条目分给标的，值一律走目录查表——模型不写
编码，也不写品目名。
"""
import json

from ict.catalog import Catalog
from ict.llm import LLMResult
from ict.schemas import AnnouncementUnderstanding, PackageUnderstanding
from ict.steps.s01_understand import checked_categories
from ict.steps.s07_normalize import normalize_candidates
from ict.steps.s08_merge import merge_projects
from test_rules import _cob

LCD = "货物/设备/办公设备/输入输出设备/液晶显示器"
FIBER = "货物/设备/通信设备/光通信设备/光通信配套设备"


class _Asker:
    """Stand-in merge model: records the payload, answers with a fixed delta list."""

    def __init__(self, deltas=()):
        self.deltas = list(deltas)
        self.payload = None

    def complete(self, *, step, prompt_version, user, thinking=False):
        self.payload = json.loads(user)
        return LLMResult(json.dumps({"deltas": self.deltas}, ensure_ascii=False),
                         "asker", "test", prompt_version, 1)


def _understanding(categories=(), packages=("1",), project_name="项目"):
    return AnnouncementUnderstanding(
        run_id="run_x",
        status="success",
        project_name=project_name,
        announcement_type="winning_announcement",
        package_mode="single" if len(packages) == 1 else "multi",
        packages=[PackageUnderstanding(package_no=no, project_id=f"{project_name}|{no}",
                                       package_evidence_text=no) for no in packages],
        announcement_categories=list(categories),
    )


def _run(candidates, deltas, categories, packages=("1",)):
    for candidate in candidates:
        candidate.project_id = f"项目|{candidate.package_no}"
    normalized = normalize_candidates("run_x", candidates, Catalog()).candidates
    asker = _Asker(deltas)
    merged = merge_projects("run_x", _understanding(categories, packages), normalized, None, llm=asker)
    return merged, asker


def _decision(merged):
    return next(item for item in merged.merge_decisions
                if item.get("op") == "category_from_announcement")


def test_a_summary_item_fills_the_blank_category_through_the_catalog():
    merged, asker = _run(
        [_cob("c1", "html", 100, {"object_name": "LCD显示大屏", "brand": "创维"})],
        [{"op": "category_from_announcement", "group_id": 0, "raw_item": LCD,
          "reason": "公告概要里这条品目说的就是这台显示大屏", "confidence": 0.8}],
        [LCD, FIBER])
    row = merged.projects[0].cobs[0]
    assert (row.category_code, row.category_name, row.category_type) == ("A02021104", "液晶显示器", "A")
    entry = _decision(merged)
    assert entry["accepted"] is True and entry["applied"] is True
    assert entry["matched_code"] == "A02021104" and entry["match_type"] == "exact_path"
    assert entry["filled"] == ["category_code", "category_name", "category_type"]
    assert asker.payload["announcement_categories"] == [LCD, FIBER]
    assert asker.payload["candidates"][0]["candidate_id"] == "c1"


def test_the_model_only_chooses_which_item_never_what_it_says():
    """A name the announcement never wrote cannot come in through the model's mouth."""
    merged, _ = _run(
        [_cob("c1", "html", 100, {"object_name": "LCD显示大屏"})],
        [{"op": "category_from_announcement", "group_id": 0, "raw_item": "液晶显示器",
          "reason": "猜的品目名", "confidence": 0.9}],
        [LCD])
    row = merged.projects[0].cobs[0]
    assert (row.category_code, row.category_name, row.category_type) == (None, None, None)
    assert [item["reason"] for item in merged.repairs] == ["llm_schema_invalid"]


def test_an_item_the_catalog_does_not_know_is_refused_out_loud():
    odd = "货物/设备/某个目录里没有的品目"
    merged, _ = _run(
        [_cob("c1", "html", 100, {"object_name": "某设备"})],
        [{"op": "category_from_announcement", "group_id": 0, "raw_item": odd,
          "reason": "公告概要里只有这一条", "confidence": 0.7}],
        [odd])
    row = merged.projects[0].cobs[0]
    assert (row.category_code, row.category_name, row.category_type) == (None, None, None)
    entry = _decision(merged)
    assert entry["accepted"] is False and entry["reject"] == "category_unmatched"


def test_a_category_the_row_already_stated_is_never_overwritten():
    """两个标的各分到一条品目：文档里写过的那一行保持原样，只填空的那一行。"""
    merged, _ = _run(
        [_cob("c1", "html", 100, {"object_name": "光跳纤架", "category_code": "A02080504",
                                  "category_name": "光通信配套设备"}),
         _cob("c2", "html", 100, {"object_name": "LCD显示大屏", "brand": "创维"})],
        [{"op": "category_from_announcement", "group_id": 0, "raw_item": FIBER,
          "reason": "光配线设备的品目", "confidence": 0.7},
         {"op": "category_from_announcement", "group_id": 1, "raw_item": LCD,
          "reason": "显示大屏的品目", "confidence": 0.7}],
        [LCD, FIBER])
    by_name = {row.object_name: row for row in merged.projects[0].cobs}
    assert (by_name["光跳纤架"].category_code, by_name["光跳纤架"].category_name) == \
        ("A02080504", "光通信配套设备")
    assert (by_name["LCD显示大屏"].category_code, by_name["LCD显示大屏"].category_name) == \
        ("A02021104", "液晶显示器")
    entries = [item for item in merged.merge_decisions
               if item.get("op") == "category_from_announcement"]
    assert sorted(item["applied"] for item in entries) == [False, True]
    kept = next(item for item in entries if item["applied"] is False)
    assert kept["accepted"] is True and kept["filled"] == [] and kept["raw_item"] == FIBER


def test_no_summary_category_means_no_extra_model_call():
    """没有公告概要品目时，单行 HTML 包仍然不调模型，与改动前一致。"""
    merged, asker = _run(
        [_cob("c1", "html", 100, {"object_name": "服务采购", "category_name": "其他商业保险服务"})],
        [], [])
    assert asker.payload is None
    assert merged.projects[0].cobs[0].category_name == "其他商业保险服务"

    _, asker2 = _run(
        [_cob("c1", "html", 100, {"object_name": "光跳纤架", "category_code": "A02080504"})],
        [], [FIBER])
    assert asker2.payload is None


def test_the_model_is_asked_when_a_summary_item_is_the_only_source():
    """单行 HTML 包里品目空着、公告概要又有品目：这时必须问一次，否则永远填不上。"""
    _, asker = _run([_cob("c1", "html", 100, {"object_name": "LCD显示大屏"})], [], [LCD])
    assert asker.payload is not None
    assert asker.payload["announcement_categories"] == [LCD]


def test_transcription_keeps_only_what_the_summary_actually_states():
    summary = LCD + "," + FIBER
    kept, dropped = checked_categories([LCD, "音频设备"], summary)
    assert kept == [LCD] and dropped == ["音频设备"]

    # 原文自己重复（C16990000,C16990000）不算抄错，也不要报成异常
    assert checked_categories(["C16990000", "C16990000"], "C16990000,C16990000") == (["C16990000"], [])
    # 空格差别不算改写
    assert checked_categories([" 液晶显示器 "], "液晶 显示器") == (["液晶显示器"], [])
    # 公告概要本来就没写品目：模型写出来的都不算
    assert checked_categories(["液晶显示器"], None) == ([], ["液晶显示器"])
    assert checked_categories("不是数组", summary) == ([], [])
    assert checked_categories([None, ""], summary) == ([], [])
