import json
from pathlib import Path

from ict.candidates import seal_candidate
from ict.catalog import Catalog
from ict.html_context import parse_notice
from ict.money import parse_amount, parse_price_cell, parse_quantity
from ict.schemas import AnnouncementUnderstanding, PackageUnderstanding
from ict.steps.s03_plan import plan_projects
from ict.steps.s04_attach import coerce_package_scope, page_packages
from ict.steps.s07_normalize import normalize_candidates
from ict.steps.s08_merge import merge_projects

HTML = Path(r"D:\all_contest\2026_9_23_ICT\data\赛题五基准测试数据\赛题五.基准测试数据_html\t20260202_26139731.html")


def test_norm_text_strips_typesetting_noise_and_keeps_content():
    """Half-width brackets and CJK padding go; case, latin spaces and enumeration marks stay."""
    from ict.steps.s07_normalize import norm_text

    assert norm_text("遥测心电监护系统（1 拖 40）") == "遥测心电监护系统(1拖40)"
    assert norm_text("规格：外形尺寸（mm） 长5645、宽2220") == "规格：外形尺寸(mm)长5645、宽2220"
    for kept in ("BeneVision TMS30A", "SOMATOM go.Up", "NGFC FRAME", "触控一体机6台、教师办公电脑14台"):
        assert norm_text(kept) == kept


def test_a_spelling_variant_is_not_reported_as_a_merge_conflict():
    """t20260202_26140620: two documents, one product, one with a typed space inside the brackets.

    The pair already landed in one baseline group, so the only thing the difference proved was that
    grouping and comparison disagreed; it used to drag a gold-correct package into review.
    """
    merged = _merge(
        [
            _cob("cand_000006", "pdf", 90, {"object_name": "遥测心电监护系统（1 拖 40）", "quantity": "1",
                                            "unit_price": "440000", "total_price": "440000"},
                 source_class="bid_quote"),
            _cob("cand_000009", "pdf", 40, {"object_name": "遥测心电监护系统（1拖40）", "quantity": "1"},
                 source_class="tender_requirement"),
        ],
        amount=440000,
    )
    assert merged.conflicts == []
    assert merged.projects[0].cobs[0].object_name == "遥测心电监护系统(1拖40)"
    assert merged.status == "success"


def test_the_page_budget_follows_native_tables_not_page_order():
    from ict.steps.s04_attach import rank_budget

    profiles = [{"page_no": page, "tables": 0, "price_hits": 0} for page in range(1, 11)]
    profiles[7]["tables"] = 3
    profiles[7]["price_hits"] = 9
    profiles[8]["tables"] = 1
    profiles[8]["price_hits"] = 4
    # The two table pages lead the budget; whatever is left over is filled in page order.
    assert rank_budget(profiles, 3) == [1, 8, 9]


def test_a_scan_with_no_tables_and_no_price_words_keeps_the_old_front_slice():
    """Nothing about a scanned file changes: the ranking falls back to page order."""
    from ict.steps.s04_attach import _budget_pages, rank_budget
    from ict.parse.census import FileEntry

    profiles = [{"page_no": page, "tables": 0, "price_hits": 0} for page in range(1, 90)]
    assert rank_budget(profiles, 40) == list(range(1, 41))

    entry = FileEntry(file_id="a001", name="not a pdf.doc", relative_path="x.doc",
                      path=Path("x.doc"), size_bytes=1, declared_ext=".doc", fmt="ole",
                      note="", digest="", pages=90)
    assert _budget_pages(entry, 40, []) == (None, [])


def test_alias_field_names_keep_object_name():
    candidate = seal_candidate(
        candidate_id="cand_000001",
        entity_type="cob",
        project_id="项目|B",
        package_no="B",
        source_type="pdf",
        file_id="a003",
        source_priority=90,
        raw_fields={
            "item_name": "HIS子系统升级",
            "brand_model": "新蓝海",
            "quantity": "1",
            "unit_price": "400000.00",
            "total_price": "400000.00",
        },
    )
    assert candidate.fields["object_name"].raw_value == "HIS子系统升级"
    assert candidate.fields["brand"].raw_value == "新蓝海"
    assert candidate.fields["quantity"].status == "present"
    named = seal_candidate(
        candidate_id="cand_000002",
        entity_type="cob",
        project_id="项目|1",
        package_no="1",
        source_type="html",
        file_id=None,
        source_priority=70,
        raw_fields={"品目编号及品目名称": "工业机器人", "报价明细内容": "潜伏式搬运机器人"},
    )
    assert named.fields["category_name"].raw_value == "工业机器人"
    assert named.fields["object_name"].raw_value == "潜伏式搬运机器人"


def test_all_prose_sections_go_to_the_model_unfiltered():
    """2026-10-08 用户指示：正文不再筛选，整篇一次交给模型。

    旧规则（bidder_body_sections）按"公司/供应商 + （数字"挑段落，实测把 65% 的公告挑成 0 段，
    而 t20260206_26155241 的「八、其它补充事宜」里就写着「（第1包）…中标人…：宁夏隆昆…85.32分」。
    """
    html = """<div class="vF_detail_content">
    <h2>五、评审专家名单：</h2>
    <p>标包：A 青岛示例科技有限公司（55.5、58、60） 杭州示例股份有限公司（93、92、96）</p>
    <h2>六、公告期限</h2>
    <p>自本公告发布之日起1个工作日。</p>
    </div>"""
    path = Path("work/pytest-tmp/score-paragraph.html")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    sections = parse_notice(path).sections
    assert len(sections) == 2                                   # 一条都不筛
    assert any("青岛示例科技有限公司" in item["text"] for item in sections)
    assert any("公告期限" in item["title"] for item in sections)  # 旧规则会把它筛掉

    html_root = Path(r"D:\all_contest\2026_9_23_ICT\data\赛题五基准测试数据\赛题五.基准测试数据_html")
    prose = parse_notice(html_root / "t20260203_26142958.html").sections
    assert any("青岛挚璞" in item["text"] for item in prose)


def test_page_scope_stays_inside_known_packages():
    assert coerce_package_scope("unknown", ["B"], ["A", "B", "C"], "报价.pdf") == "B"
    assert coerce_package_scope("unknown", ["3", "4"], ["3", "4"], "招标文件.pdf") == "unknown"
    assert coerce_package_scope("A", ["A"], ["1"], "磋商文件.pdf") == "1"
    assert coerce_package_scope("3", ["1"], ["1"], "招标文件.pdf") == "1"
    assert coerce_package_scope("2", ["1", "2", "3", "4"], ["1", "3", "4"], "招标文件.pdf") == "unknown"
    assert coerce_package_scope("B", ["B"], ["A", "B"], "B包分项报价表.pdf") == "B"


def test_amount_wan_and_quantity():
    yuan, wan = parse_amount("￥325.031000 万元（人民币）")
    assert wan is True
    assert yuan == 3250310
    parsed = parse_price_cell("15750")
    assert parsed["value"] == 15750
    assert parsed["multiplied_by_10000"] is False
    quantity, unit, ok = parse_quantity("52块")
    assert ok and quantity == 52 and unit == "块"
    quantity, unit, ok = parse_quantity("1(套)")
    assert ok and quantity == 1 and unit == "套"


def test_html_context_packages_and_summary():
    notice = parse_notice(HTML)
    assert notice.summary["采购项目名称"].startswith("2023年工信部5G+8K")
    assert "325.031000" in notice.summary["总中标金额"]
    assert "3" in notice.body_packages
    assert "4" in notice.body_packages
    assert notice.source_project_no == "WKZB2531BJI3003278"


def test_normalize_fills_total_and_catalog_name():
    catalog = Catalog()
    candidate = seal_candidate(
        candidate_id="cand_000001",
        entity_type="cob",
        project_id="项目|1",
        package_no="1",
        source_type="html",
        file_id=None,
        source_priority=70,
        raw_fields={
            "object_name": "办公用计算机",
            "category_name": "办公用房",
            "unit_price": "10",
            "quantity": "2台",
        },
    )
    normalized = normalize_candidates("run_x", [candidate], catalog)
    fields = normalized.candidates[0].fields
    assert fields["unit"].normalized_value == "台"
    assert fields["total_price"].normalized_value == 20
    assert fields["category_code"].normalized_value == "A01010100"


def test_merge_keeps_html_price_and_takes_the_manufacturer_html_left_empty():
    """The HTML row still wins the price; the attachment now fills what the HTML did not say.

    Refusing a manufacturer from every attachment whenever the list was HTML-owned only made sense
    while the closed list decided who owned the list. Rank decides that per object now.
    """
    low = seal_candidate(
        candidate_id="cand_000001",
        entity_type="cob",
        project_id="项目|1",
        package_no="1",
        source_type="html",
        file_id=None,
        source_priority=70,
        raw_fields={"object_name": "屏幕", "brand": "创维", "unit_price": 100, "quantity": 1, "unit": "块"},
    )
    high = seal_candidate(
        candidate_id="cand_000002",
        entity_type="cob",
        project_id="项目|1",
        package_no="1",
        source_type="pdf",
        file_id="a001",
        source_priority=90,
        raw_fields={"object_name": "屏幕", "brand": "创维", "unit_price": 200, "product_supplier": "厂商甲", "quantity": 1, "unit": "块"},
    )
    winner = seal_candidate(
        candidate_id="cand_000003",
        entity_type="sub",
        project_id="项目|1",
        package_no="1",
        source_type="html",
        file_id=None,
        source_priority=80,
        raw_fields={"supplier_name": "中标公司", "score": "98.5", "is_winner": True},
    )
    for candidate in (low, high, winner):
        for field in candidate.fields.values():
            if field.status == "present":
                field.normalized_value = field.raw_value
    understanding = AnnouncementUnderstanding(
        run_id="run_x",
        status="success",
        project_name="项目",
        purchaser="单位",
        source_project_no="NO1",
        announcement_type="winning_announcement",
        package_mode="single",
        packages=[
            PackageUnderstanding(
                package_no="1",
                title="项目",
                project_id="项目|1",
                package_evidence_text="单包",
            )
        ],
        summary_amount={"raw_text": "100元", "amount_yuan": 100, "scope": "announcement", "confidence": 1},
    )
    merged = merge_projects("run_x", understanding, [low, high, winner])
    project = merged.projects[0]
    assert project.package_total_amount == 100
    assert project.cobs[0].brand == "创维"
    assert project.cobs[0].unit_price == 100
    assert project.cobs[0].product_supplier == "厂商甲"
    assert project.subs[0].cooperative_product_suppliers == ["厂商甲"]


def test_parenthetical_wan_and_requirement_text():
    from ict.candidates import observe

    yuan, wan = parse_amount("243.1310000（万元）")
    assert wan is True
    assert yuan == 2431310
    assert observe("按照招标要求提供").status == "present"
    assert observe("详见附件").status == "points_to_attachment"


def test_multivalue_anchors_capture_package_amounts():
    notice = parse_notice(HTML)
    amounts = {item["package_no"]: item["amount_yuan"] for item in notice.package_anchors if item.get("package_no")}
    assert amounts["3"] == 2431310
    assert amounts["4"] == 819000
    texts = notice.sections
    assert any("万迪科" in item["text"] for item in texts)


def test_item_number_and_supplier_assign_package():
    from ict.html_context import ParsedNotice, ParsedTable
    from ict.package_resolve import resolve_table_packages
    from ict.schemas import HtmlTable, HtmlTables

    notice = ParsedNotice(
        announcement_id="demo",
        title="示例",
        summary={},
        headings=[],
        tables=[
            ParsedTable(0, "北京络捷斯特科技发展股份有限公司", ["品目号", "品目名称"], [["1-1", "工业机器人"]], "四、主要标的信息"),
            ParsedTable(1, "", ["供应商名称"], [["北京络捷斯特科技发展股份有限公司"]], "三、中标信息"),
        ],
        package_hints=[],
        package_anchors=[{"package_no": "1", "supplier_name": "北京络捷斯特科技发展股份有限公司", "amount_yuan": 1}],
    )
    tables = HtmlTables(
        run_id="run",
        status="success",
        tables=[
            HtmlTable(table_index=0, table_role="cob_detail", package_scope="unknown", row_grain="cob", status="success"),
            HtmlTable(table_index=1, table_role="winner", package_scope="1", row_grain="supplier", status="success"),
        ],
    )
    resolve_table_packages(notice, tables, ["1", "3", "4"])
    assert tables.tables[0].package_scope == "1"


def test_service_gap_ignores_missing_brand():
    candidate = seal_candidate(
        candidate_id="cand_000009",
        entity_type="cob",
        project_id="项目|1",
        package_no="1",
        source_type="html",
        file_id=None,
        source_priority=100,
        raw_fields={"object_name": "运维服务", "category_type": "C", "total_price": "100"},
    )
    understanding = AnnouncementUnderstanding(
        run_id="run_x",
        status="success",
        project_name="项目",
        announcement_type="deal_announcement",
        package_mode="single",
        packages=[PackageUnderstanding(package_no="1", title=None, project_id="项目|1", package_evidence_text="单包")],
    )
    plans = plan_projects("run_x", understanding, [candidate], True)
    assert plans.projects[0].needs_attachment is False


def test_plan_needs_attachment_when_brand_missing():
    candidate = seal_candidate(
        candidate_id="cand_000001",
        entity_type="cob",
        project_id="项目|1",
        package_no="1",
        source_type="html",
        file_id=None,
        source_priority=70,
        raw_fields={"object_name": "屏幕"},
    )
    understanding = AnnouncementUnderstanding(
        run_id="run_x",
        status="success",
        project_name="项目",
        announcement_type="winning_announcement",
        package_mode="single",
        packages=[
            PackageUnderstanding(package_no="1", title=None, project_id="项目|1", package_evidence_text="单包")
        ],
    )
    plans = plan_projects("run_x", understanding, [candidate], True)
    assert plans.projects[0].needs_attachment is True
    assert json.loads(plans.model_dump_json())["projects"][0]["has_attachment"] is True


def _cob(cid, source, priority, fields, package="1", source_class=None, issues=None, **evidence):
    return seal_candidate(
        candidate_id=cid,
        entity_type="cob",
        project_id=f"项目|{package}",
        package_no=package,
        source_type=source,
        file_id=None if source == "html" else "a001",
        source_priority=priority,
        raw_fields=fields,
        issues=issues,
        source_class=source_class,
        **evidence,
    )


def _sub(cid, name, winner=None, priority=80, package="1"):
    fields = {"supplier_name": name}
    if winner is not None:
        fields["is_winner"] = winner
    return seal_candidate(
        candidate_id=cid,
        entity_type="sub",
        project_id=f"项目|{package}",
        package_no=package,
        source_type="html",
        file_id=None,
        source_priority=priority,
        raw_fields=fields,
    )


def _understanding(amount=None, packages=("1",), project_name="项目"):
    return AnnouncementUnderstanding(
        run_id="run_x",
        status="success",
        project_name=project_name,
        announcement_type="winning_announcement",
        package_mode="single" if len(packages) == 1 else "multi",
        packages=[
            PackageUnderstanding(
                package_no=no,
                project_id=f"{project_name}|{no}",
                package_evidence_text=no,
                package_amount=None if amount is None else {"raw_text": str(amount), "amount_yuan": amount, "scope": "package"},
            )
            for no in packages
        ],
    )


def _merge(candidates, amount=None, project_name="项目", observations=None, evidence=None, llm=None):
    for candidate in candidates:
        candidate.project_id = f"{project_name}|{candidate.package_no}"
    normalized = normalize_candidates("run_x", candidates, Catalog()).candidates
    return merge_projects("run_x", _understanding(amount, project_name=project_name), normalized,
                          observations, evidence=evidence, llm=llm)


def _merged_with_deltas(candidates, deltas, amount=None, project_name="项目", evidence=None):
    """Run step 8 with a fixed model answer, the way the real step 8 sees one package."""
    from ict.llm import FakeLLMClient

    return _merge(candidates, amount=amount, project_name=project_name, evidence=evidence,
                  llm=FakeLLMClient({"merge_candidates": json.dumps({"deltas": deltas})}))


def _obs(package_no, raw_text, file_id="a004", page_no=1):
    from ict.schemas import PackageAmountObservation

    return PackageAmountObservation(package_no=package_no, raw_text=raw_text,
                                    amount_yuan=parse_amount(raw_text)[0], file_id=file_id,
                                    file_name="开标记录表.pdf", page_no=page_no, file_class="award_detail")


def test_attachment_package_amount_fills_only_an_empty_package():
    """A1: 公告没写包金额时，附件原文里的那一句补进去，并且不参与对账换组。"""
    merged = _merge(
        [_cob("c1", "pdf", 100, {"object_name": "服务器", "unit_price": "480000.00", "quantity": "1",
                                 "unit": "台", "total_price": "480000.00"}, source_class="award_detail")],
        observations=[_obs("1", "1,486,000.00元")],
    )
    project = merged.projects[0]
    assert project.package_total_amount == 1486000.0
    assert merged.amount_origins["1"] == "attachment"
    action = [item["action"] for item in merged.repairs]
    assert "package_amount_from_attachment" in action
    filled = next(item for item in merged.repairs if item["action"] == "package_amount_from_attachment")
    assert filled["raw_text"] == "1,486,000.00元" and filled["file_id"] == "a004" and filled["page_no"] == 1
    # 行和 48 万对 148.6 万：这笔包金额按公告来源会报 underflow，按附件来源必须不报。
    from ict.steps.s08_merge import package_checks

    strict = [item["check"] for item in package_checks(project, amount_checked=True)]
    loose = [item["check"] for item in package_checks(project, amount_checked=False)]
    assert "amount_underflow" in strict and "amount_underflow" not in loose
    assert not [item for item in merged.checks if item["check"].startswith("amount_")]


def test_attachment_package_amount_never_overrides_the_announcement():
    merged = _merge(
        [_cob("c1", "pdf", 100, {"object_name": "服务器", "total_price": "999999"}, source_class="award_detail")],
        amount=1000000,
        observations=[_obs("1", "888888")],
    )
    assert merged.projects[0].package_total_amount == 1000000
    assert merged.amount_origins["1"] == "announcement"
    assert "package_amount_from_attachment" not in [item["action"] for item in merged.repairs]


def test_conflicting_attachment_amounts_are_left_unfilled():
    merged = _merge(
        [_cob("c1", "pdf", 100, {"object_name": "服务器", "total_price": "100"}, source_class="award_detail")],
        observations=[_obs("1", "1930000元", file_id="a004"), _obs("1", "1940000元", file_id="a006")],
    )
    assert merged.projects[0].package_total_amount is None
    assert merged.amount_origins["1"] == "conflict"
    assert "package_amount_conflict" in [item["action"] for item in merged.repairs]


def test_attachment_amount_is_not_coped_onto_a_lone_object():
    """单标的补包金额那条规则只能用公告金额，不能用同一份附件来的数。"""
    merged = _merge(
        [_cob("c1", "pdf", 100, {"object_name": "服务器"}, source_class="award_detail")],
        observations=[_obs("1", "1486000")],
    )
    assert merged.projects[0].package_total_amount == 1486000.0
    assert merged.projects[0].cobs[0].total_price is None
    assert "total_from_package_amount" not in [item["action"] for item in merged.repairs]


def test_observation_for_an_unknown_package_never_fills():
    merged = _merge(
        [_cob("c1", "pdf", 100, {"object_name": "服务器", "total_price": "100"}, source_class="award_detail")],
        observations=[_obs("Z", "100万")],
    )
    assert merged.projects[0].package_total_amount is None
    assert "package_amount_from_attachment" not in [item["action"] for item in merged.repairs]


def test_amount_picks_attachment_bundle_over_headcount_quantity():
    merged = _merge(
        [
            _cob("cand_000001", "html", 100, {"object_name": "团体重大疾病保险", "unit_price": "194,679.00", "quantity": "1338人"}),
            _cob("cand_000002", "html", 100, {"object_name": "团体意外伤害保险", "unit_price": "1,027,584.00", "quantity": "1338人"}),
            _cob("cand_000003", "pdf", 100, {"object_name": "团体重大疾病保险", "unit_price": "194,679.00", "quantity": "1.0000", "unit": "项", "total_price": "194,679.00"}, source_class="award_detail"),
            _cob("cand_000004", "pdf", 100, {"object_name": "团体意外伤害保险", "unit_price": "1,027,584.00", "quantity": "1.0000", "unit": "项", "total_price": "1,027,584.00"}, source_class="award_detail"),
        ],
        amount=1222263,
    )
    cobs = {cob.object_name: cob for cob in merged.projects[0].cobs}
    assert cobs["团体重大疾病保险"].quantity == 1 and cobs["团体重大疾病保险"].unit == "项"
    assert cobs["团体重大疾病保险"].total_price == 194679
    assert cobs["团体意外伤害保险"].total_price == 1027584
    assert [item["action"] for item in merged.repairs] == ["price_bundle_by_amount", "price_bundle_by_amount"]


def test_html_detail_still_outranks_a_self_contradictory_attachment_row():
    """The 5% row tolerance is gone, so rank decides, and both readings stay on the record.

    The attachment row says 1500 x 6 = 90000. Nothing demotes it for that any more; the HTML detail
    row simply outranks it. The contradiction is not hidden - it is written into alternatives, which
    is what step 8b and a reviewer look at.
    """
    merged = _merge(
        [
            _cob("cand_000001", "html", 100, {"object_name": "触控笔", "brand": "极倍", "unit_price": "1,500.0000", "quantity": "6", "unit": "个", "total_price": "9,000.00"}),
            _cob("cand_000002", "pdf", 90, {"object_name": "触控笔", "brand": "极倍", "unit_price": "1,500.00000元", "quantity": "6.0000", "unit": "个", "total_price": "9,0000元", "制造商": "极倍信息科技"}),
            _cob("cand_000003", "pdf", 90, {"object_name": "触控笔", "quantity": "6.0000", "unit": "个"}),
        ]
    )
    cobs = merged.projects[0].cobs
    assert len(cobs) == 1
    assert cobs[0].total_price == 9000
    assert cobs[0].product_supplier == "极倍信息科技"
    totals = sorted(option["total_price"] for option in merged.alternatives["项目|1"][0]["options"])
    assert totals == [9000, 90000]


def test_project_row_price_moves_to_the_only_object():
    merged = _merge(
        [
            _cob("cand_000001", "html", 100, {"object_name": "水库运行维护"}),
            _cob("cand_000002", "html", 100, {"object_name": "水库运行维护"}),
            _cob("cand_000003", "html", 100, {"object_name": "济南市莱芜区2026年度中型水库运行维护项目", "total_price": "376600"}),
        ],
        amount=376600,
        project_name="济南市莱芜区2026年度中型水库运行维护项目",
    )
    cobs = merged.projects[0].cobs
    assert [(cob.object_name, cob.total_price) for cob in cobs] == [("水库运行维护", 376600)]
    assert merged.list_sources["1"] == "html"


def test_a_project_named_row_is_not_an_object_whichever_side_it_came_from():
    """t20260508_26523336: the attachment repeats the project name and carries a bid price.

    The closed list used to stop this by demanding the name match an HTML row. The project-row rule
    stops it on both sides now, and the bid price of 379207.16 never reaches the list.
    """
    merged = _merge(
        [
            _cob("cand_000001", "html", 100, {"object_name": "水库运行维护"}),
            _cob("cand_000002", "html", 100, {"object_name": "济南市莱芜区2026年度中型水库运行维护项目", "total_price": "376600"}),
            _cob("cand_000003", "pdf", 90, {"object_name": "济南市莱芜区2026年度中型水库运行维护项目", "total_price": "379207.16"}, source_class="bid_quote"),
        ],
        amount=376600,
        project_name="济南市莱芜区2026年度中型水库运行维护项目",
    )
    cobs = merged.projects[0].cobs
    assert [(cob.object_name, cob.total_price) for cob in cobs] == [("水库运行维护", 376600)]
    reasons = [item["reason"] for item in merged.unmatched_summary_rows]
    assert reasons.count("项目全称行，价格并入唯一标的") == 1
    assert reasons.count("项目全称行不作为标的") == 1


def test_an_attachment_object_is_no_longer_barred_by_its_name():
    merged = _merge(
        [
            _cob("cand_000001", "html", 100, {"object_name": "水库运行维护", "total_price": "376600"}),
            _cob("cand_000002", "pdf", 90, {"object_name": "库区巡查", "quantity": "1", "unit_price": "1000"}, source_class="bid_quote"),
            _cob("cand_000003", "pdf", 40, {"object_name": "电缆更换", "quantity": "1"}, source_class="tender_requirement"),
        ],
        amount=376600,
    )
    assert [cob.object_name for cob in merged.projects[0].cobs] == ["水库运行维护", "库区巡查"]
    reasons = {item["candidate_id"]: item["reason"] for item in merged.unmatched_summary_rows}
    assert reasons["cand_000003"] == "tender_requirement"
    assert merged.list_sources["1"] == "mixed"


def test_single_object_takes_package_amount():
    merged = _merge([_cob("cand_000001", "html", 100, {"object_name": "系统运维"})], amount=998000)
    assert merged.projects[0].cobs[0].total_price == 998000
    assert merged.repairs[0]["action"] == "total_from_package_amount"


def test_attachment_rows_join_the_list_without_a_rule_deciding_who_owns_it():
    """t20260202_26140146 in shape: the HTML names one object and points every field elsewhere."""
    merged = _merge(
        [
            _cob("cand_000001", "html", 100, {"object_name": "护理信息化系统", "brand": "详见附件", "quantity": "详见附件"}),
            _cob("cand_000002", "pdf", 90, {"object_name": "HIS子系统升级", "brand": "新蓝海", "quantity": "1", "unit_price": "400000", "制造商": "新蓝海"}, source_class="bid_quote"),
            _cob("cand_000003", "pdf", 90, {"object_name": "经济核算", "quantity": "1", "unit_price": "100000"}, source_class="bid_quote"),
        ]
    )
    cobs = merged.projects[0].cobs
    assert [cob.object_name for cob in cobs] == ["HIS子系统升级", "经济核算"]
    assert cobs[0].product_supplier == "新蓝海"
    assert [item["reason"] for item in merged.unmatched_summary_rows] == ["html_pointer_row"]


def test_a_losing_bidder_quote_fills_fields_but_neither_prices_nor_objects():
    from ict.schemas import MergeEvidence

    evidence = MergeEvidence(winner_hints={"1": ["甲公司"]}, file_names={"a001": "乙公司分项报价表.pdf"})
    merged = _merge(
        [
            _cob("cand_000001", "html", 100, {"object_name": "服务器"}),
            _cob("cand_000002", "pdf", 90, {"object_name": "服务器", "brand": "浪潮", "unit_price": "88888", "total_price": "88888"},
                 source_class="bid_quote", quote_supplier="乙公司"),
            _cob("cand_000003", "pdf", 90, {"object_name": "交换机", "unit_price": "5000", "total_price": "5000"},
                 source_class="bid_quote", quote_supplier="乙公司"),
        ],
        evidence=evidence,
    )
    cobs = merged.projects[0].cobs
    assert [cob.object_name for cob in cobs] == ["服务器"]
    assert cobs[0].brand == "浪潮"
    assert cobs[0].total_price is None
    reasons = {item["candidate_id"]: item["reason"] for item in merged.unmatched_summary_rows}
    assert reasons["cand_000003"] == "non_winner_quote"


def test_the_winners_own_quote_may_supply_the_whole_list():
    """The award detail often *is* the winner's itemized quote; barring bid_quote loses the list."""
    from ict.schemas import MergeEvidence

    evidence = MergeEvidence(winner_hints={"1": ["甲公司"]})
    merged = _merge(
        [
            _cob("cand_000001", "html", 100, {"object_name": "护理信息化系统", "quantity": "详见附件"}),
            _cob("cand_000002", "pdf", 90, {"object_name": "HIS子系统升级", "quantity": "1", "unit_price": "400000", "total_price": "400000"},
                 source_class="bid_quote", quote_supplier="甲公司"),
        ],
        amount=400000,
        evidence=evidence,
    )
    cobs = merged.projects[0].cobs
    assert [cob.object_name for cob in cobs] == ["HIS子系统升级"]
    assert cobs[0].total_price == 400000


def test_underflow_is_information_not_a_defect():
    from ict.steps.s08_merge import consistency_checks

    merged = _merge(
        [
            _cob("cand_000001", "html", 100, {"object_name": "光跳纤架", "unit_price": "319300", "quantity": "4套"}),
        ],
        amount=2431310,
    )
    merged.projects[0].subs = []
    failures = consistency_checks(merged)
    levels = {item["check"]: item["level"] for item in merged.checks}
    # Gold for t20260202_26139731 package 3 is a line sum 35% under the amount, so a shortfall
    # cannot block a run. missing_winner is what blocks this one.
    assert levels["amount_underflow"] == "soft"
    assert levels["missing_winner"] == "hard"
    assert failures and "amount_underflow" not in failures[0].failure_message


def test_unpriced_rows_are_reported_instead_of_passing_silently():
    """t20260812_27119852: six objects, no price on any of them, and the run reported success."""
    from ict.steps.s08_merge import consistency_checks

    merged = _merge(
        [
            _cob("cand_000001", "html", 100, {"object_name": "景区日常维护"}),
            _cob("cand_000002", "html", 100, {"object_name": "景区环境整治"}),
        ],
        amount=3225000,
    )
    failures = consistency_checks(merged)
    levels = {item["check"]: item["level"] for item in merged.checks}
    assert levels["missing_line_price"] == "medium"
    assert [item["cob_indexes"] for item in merged.checks if item["check"] == "missing_line_price"] == [[0, 1]]
    assert "amount_underflow" not in levels
    assert failures


def test_winner_table_marks_is_winner():
    from ict.html_context import ParsedNotice, ParsedTable
    from ict.llm import FakeLLMClient
    from ict.schemas import HtmlTable, HtmlTables
    from ict.steps.s02_html import extract_html_candidates

    notice = ParsedNotice(
        announcement_id="demo",
        title="示例",
        summary={},
        headings=[],
        tables=[
            ParsedTable(
                0,
                "采购包1:",
                ["供应商名称", "供应商地址", "中标（成交）金额"],
                [["甲公司", "地址", "100"], ["评审总得分及排名情况", "乙公司56.29分排名第二"]],
                "三、中标信息",
            )
        ],
        package_hints=[],
    )
    tables = HtmlTables(
        run_id="run",
        status="success",
        tables=[HtmlTable(table_index=0, table_role="winner", package_scope="1", row_grain="supplier", status="success")],
    )
    rows = [
        {"entity_type": "sub", "fields": {"supplier_name": "甲公司"}},
        {"entity_type": "sub", "fields": {"supplier_name": "乙公司", "is_winner": False}},
    ]
    llm = FakeLLMClient({"extract_html_candidates": json.dumps({"candidates": rows})})
    candidates, _, _ = extract_html_candidates("run", notice, tables, "项目", llm, known_packages=["1"])
    assert candidates[0].fields["is_winner"].raw_value is True
    assert candidates[1].fields["is_winner"].raw_value is False


def test_promotion_needs_structure_and_respects_vetoes():
    from ict.html_context import ParsedNotice, ParsedTable
    from ict.package_resolve import promote_object_tables
    from ict.schemas import HtmlTable, HtmlTables

    notice = ParsedNotice(
        announcement_id="demo",
        title="示例",
        summary={},
        headings=[],
        tables=[
            ParsedTable(0, "", ["字段", "内容"], [["名称", "系统运维"], ["服务范围", "运维"]], "四、主要标的信息", True),
            ParsedTable(1, "", ["序号", "项目名称", "甲方信息", "竣工验收时间"], [["1", "往年项目", "水务局", "2024年"]], "九、其他补充事宜"),
            ParsedTable(2, "", ["序号", "供应商名称", "未中标原因"], [["1", "乙公司", "得分较低"]], "九、其他补充事宜"),
            ParsedTable(3, "", ["序号", "文件类型", "文件名称", "可下载时间"], [["1", ".pdf", "报价.pdf", ""]], "四、主要标的信息"),
        ],
        package_hints=[],
    )
    tables = HtmlTables(
        run_id="run",
        status="success",
        tables=[
            HtmlTable(table_index=0, table_role="other", package_scope="1", row_grain="project", status="success"),
            HtmlTable(table_index=1, table_role="other", package_scope="1", row_grain="other", status="success"),
            HtmlTable(table_index=2, table_role="other", package_scope="1", row_grain="supplier", status="success"),
            HtmlTable(table_index=3, table_role="cob_detail", package_scope="1", row_grain="cob", status="success"),
        ],
    )
    promote_object_tables(notice, tables)
    assert [item.table_role for item in tables.tables] == ["cob_detail", "other", "other", "other"]
    assert "promoted_by:section" in tables.tables[0].issues
    assert "demoted_non_object" in tables.tables[3].issues


def test_repair_marks_winner_and_accepts_only_better_model_choice():
    from ict.llm import FakeLLMClient
    from ict.steps.s08b_repair import repair_packages

    merged = _merge(
        [
            _cob("cand_000001", "html", 100, {"object_name": "设备", "unit_price": "100", "quantity": "10"}),
            _cob("cand_000002", "pdf", 90, {"object_name": "设备", "unit_price": "100", "quantity": "1", "total_price": "100"}, source_class="bid_quote"),
            _sub("cand_000003", "甲公司", priority=70),
        ],
        amount=100,
    )
    merged.repairs.clear()
    merged.alternatives["项目|1"][0]["chosen"] = "cand_000001"
    project = merged.projects[0]
    project.cobs[0].quantity, project.cobs[0].total_price = 10, 1000
    bad = FakeLLMClient({"repair_packages": json.dumps({"choices": [{"cluster_id": 0, "candidate_id": "cand_000001"}]})})
    repair_packages(merged, {"1": ["甲公司"]}, bad)
    assert merged.projects[0].subs[0].is_winner is True
    assert merged.projects[0].cobs[0].total_price == 1000
    assert merged.repairs[-1]["action"] == "model_choice_rejected"
    good = FakeLLMClient({"repair_packages": json.dumps({"choices": [{"cluster_id": 0, "candidate_id": "cand_000002"}]})})
    failures = repair_packages(merged, {}, good)
    assert merged.projects[0].cobs[0].total_price == 100
    assert merged.repairs[-1]["action"] == "price_bundle_by_model"
    assert not failures


def test_page_naming_two_packages_is_announcement_scope():
    from ict.steps.s04_attach import page_packages

    text = "## 一、合同包1：供热监管平台\n总价9,281,000.00元\n## 二、合同包2：工程监理\n总价195,000.00元"
    assert page_packages(text, ["1", "2"]) == {"1", "2"}
    assert page_packages("第1包 分项报价表 包括A类设备", ["1", "2"]) == {"1"}


def test_joint_venture_spellings_merge_under_html_name():
    merged = _merge(
        [
            _cob("cand_000001", "html", 100, {"object_name": "平台", "total_price": "100"}),
            _sub("cand_000002", "甲研究院(联合体成员：乙大学)", winner=True, priority=70),
            seal_candidate(
                candidate_id="cand_000003",
                entity_type="sub",
                project_id="项目|1",
                package_no="1",
                source_type="pdf",
                file_id="a003",
                source_priority=100,
                raw_fields={"supplier_name": "甲研究院（联投体成员：乙大学）", "is_winner": True},
            ),
        ]
    )
    subs = merged.projects[0].subs
    assert [sub.supplier_name for sub in subs] == ["甲研究院(联合体成员：乙大学)"]
    assert not [item for item in merged.conflicts if item["field"] == "supplier_name"]


def test_repair_adds_missing_winner_from_award_text():
    from ict.steps.s08b_repair import repair_packages

    merged = _merge([_cob("cand_000001", "html", 100, {"object_name": "LCD显示大屏", "unit_price": "15750", "quantity": "52块"})], amount=819000)
    failures = repair_packages(merged, {"1": ["北京智为视媒科技有限公司"]}, None)
    subs = merged.projects[0].subs
    assert [(sub.supplier_name, sub.is_winner) for sub in subs] == [("北京智为视媒科技有限公司", True)]
    assert merged.repairs[-1]["action"] == "winner_added_from_award_text"
    assert not failures


def test_bidder_column_is_not_product_supplier():
    from ict.html_context import ParsedNotice, ParsedTable
    from ict.llm import FakeLLMClient
    from ict.schemas import HtmlTable, HtmlTables
    from ict.steps.s02_html import extract_html_candidates

    headers = ["序号", "供应商名称", "货物名称", "货物品牌"]
    notice = ParsedNotice(
        announcement_id="demo",
        title="示例",
        summary={},
        headings=[],
        tables=[ParsedTable(0, "", headers, [["1", "第4包 LCD显示大屏：北京智为视媒科技有限公司", "LCD显示大屏", "创维"]], "三、中标信息")],
        package_hints=[],
    )
    mapping = {"supplier_name": "供应商名称", "object_name": "货物名称", "brand": "货物品牌"}
    tables = HtmlTables(
        run_id="run",
        status="success",
        tables=[HtmlTable(table_index=0, table_role="cob_detail", package_scope="1", row_grain="cob", column_mapping=mapping, status="success")],
    )
    row = {"entity_type": "cob", "fields": {"object_name": "LCD显示大屏", "brand": "创维", "product_supplier": "北京智为视媒科技有限公司"}}
    llm = FakeLLMClient({"extract_html_candidates": json.dumps({"candidates": [row]})})
    candidates, _, _ = extract_html_candidates("run", notice, tables, "项目", llm, known_packages=["1"])
    assert candidates[0].fields["product_supplier"].raw_value is None
    assert "product_supplier_from_bidder_column" in candidates[0].issues

# --------------------------------------------------------------------------------------
# Step 8: the model proposes deltas, rules own the arithmetic and the veto.
# --------------------------------------------------------------------------------------

def test_a_percentage_is_never_an_amount():
    """t20260812_27119852 wrote 折扣率：96.60% into package_amount and it became 96.6 yuan."""
    assert parse_amount("折扣率：96.60%")[0] is None
    assert parse_amount("下浮率3%")[0] is None
    assert parse_amount("945000元")[0] == 945000


def test_a_thousands_comma_split_by_ocr_space_still_reads_as_one_number():
    """t20260401_26350252：OCR 把 532,012 读成「532, 012」，曾静默变成 532（差 1000 倍）。

    只合"逗号 + 可选空格 + 恰好三位"这一种形态；'1, 2' 这类列表不满足三位，按原样处理。
    """
    assert parse_amount("532, 012")[0] == 532012
    assert parse_amount("532, 012.00")[0] == 532012
    assert parse_amount("1,486,000.00元")[0] == 1486000
    assert parse_amount("1, 2")[0] == 1          # 列表形态不并
    assert parse_amount("1, 23")[0] == 1


def test_a_percentage_never_becomes_a_package_amount():
    """t20260812_27119852: the model copied 折扣率：96.60% and it was used as the package total.

    The guard sits where the number is made, in step 1, not where it is spent. Step 8 only refuses
    to arbitrate with an amount that is not positive.
    """
    from ict.steps.s01_understand import seal

    understanding = seal("run_x", {
        "project_name": "项目",
        "package_mode": "single",
        "packages": [{"package_no": "1", "package_amount": {"raw_text": "折扣率：96.60%"}}],
    }, None, [])
    assert understanding.packages[0].package_amount.amount_yuan is None

    quarantined = _merge(
        [_cob("cand_000001", "html", 100, {"object_name": "景区维护", "total_price": "945000"})],
        amount=0,
    )
    assert quarantined.projects[0].package_total_amount is None
    assert quarantined.amount_audit["1"]["suspect"] == "non_positive"
    assert "total_from_package_amount" not in [item["action"] for item in quarantined.repairs]


def test_the_amount_picks_between_two_bundles_a_tenth_of_a_percent_apart():
    """t20260401_26346106: 1241056.19 and 1239629.63 for one object, and the amount is the second.

    The 8% dead zone used to skip this entirely, because the two readings are 0.1% apart.
    """
    merged = _merge(
        [
            _cob("cand_000001", "html", 100, {"object_name": "公务用车运营服务"}),
            _cob("cand_000002", "pdf", 90, {"object_name": "公务用车运营服务", "total_price": "1241056.19"}),
            _cob("cand_000003", "pdf", 90, {"object_name": "公务用车运营服务", "total_price": "1239629.63"}),
        ],
        amount=1239629.63,
    )
    assert merged.projects[0].cobs[0].total_price == 1239629.63
    swaps = [item for item in merged.repairs if item["action"] == "price_bundle_by_amount"]
    assert len(swaps) == 1 and swaps[0]["to"] == "cand_000003"


def test_a_template_placeholder_never_becomes_a_price():
    """t20260806_27088832 put '{=响应报价/数量} 元' into alternatives, where 8b could have picked it."""
    merged = _merge(
        [
            _cob("cand_000001", "html", 100, {"object_name": "研讨服务", "quantity": "1", "unit": "项",
                                              "unit_price": "494850", "total_price": "494850"}),
            _cob("cand_000002", "pdf", 90, {"object_name": "研讨服务", "quantity": "1", "unit": "项",
                                            "unit_price": "{=响应报价/数量} 元", "total_price": "{供应商响应} 元"},
                 source_class="bid_quote"),
        ],
        amount=494850,
    )
    assert merged.projects[0].cobs[0].total_price == 494850
    assert not (merged.alternatives.get("项目|1") or [])


def test_a_merge_delta_joins_rows_the_baseline_could_not():
    """t20260812_27119852: an em dash read as the character 一 cost six objects their prices."""
    merged = _merged_with_deltas(
        [
            _cob("cand_000001", "html", 100, {"object_name": "城维计划—景区日常维护"}),
            _cob("cand_000002", "pdf", 90, {"object_name": "城维计划一景区日常维护", "quantity": "1", "unit": "项",
                                            "unit_price": "950000", "total_price": "950000"}, source_class="bid_quote"),
        ],
        [{"op": "merge", "groups": [0, 1], "reason": "破折号被识别成了一"}],
        amount=950000,
    )
    cobs = merged.projects[0].cobs
    assert len(cobs) == 1 and cobs[0].total_price == 950000
    accepted = [item for item in merged.merge_decisions if item["accepted"]]
    assert [item["op"] for item in accepted] == ["merge"]


def test_a_split_delta_separates_rows_that_only_share_a_name():
    """t20260807_27097806: three different objects, all called 智能建造设备 in the HTML."""
    merged = _merged_with_deltas(
        [
            _cob("cand_000001", "html", 100, {"object_name": "智能建造设备", "spec_model": "3D混凝土打印机",
                                              "quantity": "1", "unit": "套"}),
            _cob("cand_000002", "html", 100, {"object_name": "智能建造设备", "spec_model": "机器人实训平台",
                                              "quantity": "2", "unit": "台"}),
        ],
        [{"op": "split", "group_id": 0, "parts": [["cand_000001"], ["cand_000002"]], "reason": "规格不同"}],
    )
    cobs = merged.projects[0].cobs
    assert len(cobs) == 2
    assert sorted(cob.spec_model for cob in cobs) == ["3D混凝土打印机", "机器人实训平台"]


def test_a_delta_that_breaks_a_rule_is_dropped_alone():
    """One bad edit costs that edit. The rest of the model's answer still stands."""
    from ict.schemas import MergeEvidence

    evidence = MergeEvidence(winner_hints={"1": ["甲公司"]})
    merged = _merged_with_deltas(
        [
            _cob("cand_000001", "html", 100, {"object_name": "服务器", "total_price": "100"}),
            _cob("cand_000002", "pdf", 90, {"object_name": "服务器", "total_price": "999"},
                 source_class="bid_quote", quote_supplier="乙公司"),
        ],
        [{"op": "price_from", "group_id": 0, "candidate_id": "cand_000002", "reason": "看起来更细"},
         {"op": "name_from", "group_id": 0, "candidate_id": "cand_000002", "reason": "写法一样"}],
        amount=100,
        evidence=evidence,
    )
    by_op = {item["op"]: item for item in merged.merge_decisions if item["op"] in {"price_from", "name_from"}}
    assert by_op["price_from"]["accepted"] is False
    assert by_op["price_from"]["reject"] == "source_cannot_own_price"
    assert by_op["name_from"]["accepted"] is True
    assert merged.projects[0].cobs[0].total_price == 100


def test_an_unknown_id_costs_the_call_and_the_baseline_still_decides():
    merged = _merged_with_deltas(
        [
            _cob("cand_000001", "html", 100, {"object_name": "服务器", "total_price": "100"}),
            _cob("cand_000002", "pdf", 90, {"object_name": "伺服器", "total_price": "100"}, source_class="bid_quote"),
        ],
        [{"op": "merge", "groups": [0, 99], "reason": "编号写错了"}],
        amount=100,
    )
    assert [cob.object_name for cob in merged.projects[0].cobs] == ["服务器", "伺服器"]
    assert {"package_no": "1", "action": "merge_model_fallback", "reason": "llm_schema_invalid"} in merged.repairs


def _split_rows():
    return [
        _cob("cand_000001", "pdf", 90, {"object_name": "办公桌", "quantity": "10", "unit": "张",
                                        "unit_price": "1000", "total_price": "10000"}, source_class="award_detail"),
        _cob("cand_000002", "pdf", 90, {"object_name": "办公桌", "quantity": "5", "unit": "张",
                                        "unit_price": "1000", "total_price": "5000"}, source_class="award_detail"),
    ]


def test_a_sum_is_recomputed_by_rules_and_gated_by_the_amount():
    """The model names the rows; 15 and 15000 are computed here, and only because the amount agrees."""
    merged = _merged_with_deltas(
        _split_rows(),
        [{"op": "sum", "group_id": 0, "members": ["cand_000001", "cand_000002"], "reason": "同一张表拆行"}],
        amount=15000,
    )
    cobs = merged.projects[0].cobs
    assert len(cobs) == 1
    assert (cobs[0].quantity, cobs[0].unit_price, cobs[0].total_price) == (15, 1000, 15000)
    assert any(item["action"] == "price_bundle_by_amount" and item["to"] == "sum:0" for item in merged.repairs)


def test_a_sum_is_refused_when_the_units_disagree():
    """1批 and 20台 cannot be added, whatever the model thinks the rows mean."""
    merged = _merged_with_deltas(
        [
            _cob("cand_000001", "pdf", 90, {"object_name": "运维服务", "quantity": "1", "unit": "批",
                                            "unit_price": "1000", "total_price": "1000"}, source_class="award_detail"),
            _cob("cand_000002", "pdf", 90, {"object_name": "运维服务", "quantity": "20", "unit": "台",
                                            "unit_price": "50", "total_price": "1000"}, source_class="award_detail"),
        ],
        [{"op": "sum", "group_id": 0, "members": ["cand_000001", "cand_000002"], "reason": "看起来是拆行"}],
        amount=2000,
    )
    assert len(merged.projects[0].cobs) == 1
    assert "sum_unit_conflict" in [item.get("reject") for item in merged.merge_decisions if item["op"] == "sum"]


def test_a_sum_is_left_alone_without_a_package_amount():
    """Nothing to check the sum against, so the object stays one row and keeps an observed number."""
    merged = _merged_with_deltas(
        _split_rows(),
        [{"op": "sum", "group_id": 0, "members": ["cand_000001", "cand_000002"], "reason": "同一张表拆行"}],
    )
    cobs = merged.projects[0].cobs
    assert len(cobs) == 1 and cobs[0].quantity == 10
    rejects = [item.get("reject") for item in merged.merge_decisions if item["op"] == "sum"]
    assert "sum_unverified_no_package_amount" in rejects


def test_the_result_does_not_depend_on_the_order_candidates_arrive_in():
    def pair():
        return [
            _cob("cand_000001", "html", 100, {"object_name": "屏幕", "unit_price": "100", "quantity": "1",
                                              "unit": "块", "total_price": "100"}),
            _cob("cand_000002", "pdf", 90, {"object_name": "屏幕", "unit_price": "200", "quantity": "1",
                                            "unit": "块", "total_price": "200"}, source_class="bid_quote"),
        ]

    forward = _merge(pair(), amount=200)
    backward = _merge(list(reversed(pair())), amount=200)
    assert [cob.model_dump() for cob in forward.projects[0].cobs] == \
        [cob.model_dump() for cob in backward.projects[0].cobs]
    assert forward.projects[0].cobs[0].total_price == 200
