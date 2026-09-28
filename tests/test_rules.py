import json
from pathlib import Path

from ict.candidates import seal_candidate
from ict.catalog import Catalog
from ict.html_context import bidder_body_sections, parse_notice
from ict.money import parse_amount, parse_price_cell, parse_quantity
from ict.schemas import AnnouncementUnderstanding, PackageUnderstanding
from ict.steps.s03_plan import plan_projects
from ict.steps.s04_attach import coerce_package_scope
from ict.steps.s07_normalize import normalize_candidates
from ict.steps.s08_merge import merge_projects

HTML = Path(r"D:\all_contest\2026_9_23_ICT\data\赛题五基准测试数据\赛题五.基准测试数据_html\t20260202_26139731.html")


def test_missing_markdown_is_paddle_not_connected(monkeypatch, tmp_path):
    from ict import documents
    from ict.documents import MarkdownUnavailable, ensure_markdown

    pdf_root = tmp_path / "attachments" / "demo"
    md_root = tmp_path / "attachments-md"
    pdf_root.mkdir(parents=True)
    pdf = pdf_root / "报价.pdf"
    pdf.write_bytes(b"%PDF")
    monkeypatch.setattr(documents, "ATTACHMENTS_ROOT", tmp_path / "attachments")
    monkeypatch.setattr(documents, "ATTACHMENTS_MD_ROOT", md_root)
    monkeypatch.delenv("ICT_PADDLE_COMMAND", raising=False)
    try:
        ensure_markdown(pdf)
    except MarkdownUnavailable as exc:
        assert exc.reason == "paddle_not_connected"
    else:
        raise AssertionError("expected MarkdownUnavailable")


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


def test_bidder_score_paragraph_is_kept():
    html = """<div class="vF_detail_content">
    <h2>五、评审专家名单：</h2>
    <p>标包：A 青岛示例科技有限公司（55.5、58、60） 杭州示例股份有限公司（93、92、96）</p>
    </div>"""
    path = Path("work/pytest-tmp/score-paragraph.html")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    texts = bidder_body_sections(parse_notice(path))
    assert len(texts) == 1
    assert "青岛示例科技有限公司" in texts[0]["text"]
    html_root = Path(r"D:\all_contest\2026_9_23_ICT\data\赛题五基准测试数据\赛题五.基准测试数据_html")
    prose = bidder_body_sections(parse_notice(html_root / "t20260203_26142958.html"))
    assert any("青岛挚璞" in item["text"] for item in prose)
    assert any("未中标" in item["title"] or "青岛挚璞" in item["text"] for item in prose)
    experts = bidder_body_sections(parse_notice(html_root / "t20260202_26139917.html"))
    assert all("公司" in item["text"] or "供应商名称" in item["text"] for item in experts)


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


def test_merge_keeps_html_price_and_list_owner_product_supplier():
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
        summary_amount={"raw_text": "10元", "amount_yuan": 10, "scope": "announcement", "confidence": 1},
    )
    merged = merge_projects("run_x", understanding, [low, high, winner])
    project = merged.projects[0]
    assert project.package_total_amount == 10
    assert project.cobs[0].brand == "创维"
    assert project.cobs[0].unit_price == 100
    assert project.cobs[0].product_supplier is None
    assert project.subs[0].cooperative_product_suppliers == []


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
    texts = bidder_body_sections(notice)
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


def _cob(cid, source, priority, fields, package="1", source_class=None, issues=None):
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


def _merge(candidates, amount=None, project_name="项目"):
    for candidate in candidates:
        candidate.project_id = f"{project_name}|{candidate.package_no}"
    normalized = normalize_candidates("run_x", candidates, Catalog()).candidates
    return merge_projects("run_x", _understanding(amount, project_name=project_name), normalized)


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


def test_row_mismatch_bundle_does_not_win():
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
    assert cobs[0].product_supplier is None


def test_project_row_price_moves_to_single_object():
    merged = _merge(
        [
            _cob("cand_000001", "html", 100, {"object_name": "水库运行维护"}),
            _cob("cand_000002", "html", 100, {"object_name": "水库运行维护"}),
            _cob("cand_000003", "html", 100, {"object_name": "济南市莱芜区2026年度中型水库运行维护项目", "total_price": "376600"}),
            _cob("cand_000004", "pdf", 90, {"object_name": "库区巡查", "quantity": "1", "unit_price": "1000"}, source_class="bid_quote"),
            _cob("cand_000005", "pdf", 40, {"object_name": "电缆更换", "quantity": "1"}, source_class="tender_requirement"),
        ],
        amount=376600,
        project_name="济南市莱芜区2026年度中型水库运行维护项目",
    )
    cobs = merged.projects[0].cobs
    assert [(cob.object_name, cob.total_price) for cob in cobs] == [("水库运行维护", 376600)]
    assert merged.list_sources["1"] == "html"


def test_single_object_takes_package_amount():
    merged = _merge([_cob("cand_000001", "html", 100, {"object_name": "系统运维"})], amount=998000)
    assert merged.projects[0].cobs[0].total_price == 998000
    assert merged.repairs[0]["action"] == "total_from_package_amount"


def test_open_list_only_when_line_fields_point_to_attachment():
    pointed = _merge(
        [
            _cob("cand_000001", "html", 100, {"object_name": "护理信息化系统", "brand": "详见附件", "quantity": "详见附件"}),
            _cob("cand_000002", "pdf", 90, {"object_name": "HIS子系统升级", "brand": "新蓝海", "quantity": "1", "unit_price": "400000", "制造商": "新蓝海"}, source_class="bid_quote"),
            _cob("cand_000003", "pdf", 90, {"object_name": "经济核算", "quantity": "1", "unit_price": "100000"}, source_class="bid_quote"),
        ]
    )
    names = [cob.object_name for cob in pointed.projects[0].cobs]
    assert names == ["HIS子系统升级", "经济核算"]
    assert pointed.projects[0].cobs[0].product_supplier == "新蓝海"
    assert pointed.list_sources["1"] == "attachment"
    described = _merge(
        [
            _cob("cand_000001", "html", 100, {"object_name": "水库运行维护"}),
            _cob("cand_000002", "pdf", 90, {"object_name": "库区巡查", "quantity": "1", "unit_price": "1000"}, source_class="bid_quote"),
        ]
    )
    assert [cob.object_name for cob in described.projects[0].cobs] == ["水库运行维护"]


def test_underflow_on_html_list_is_soft():
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
    assert levels["amount_underflow"] == "soft"
    assert levels["missing_winner"] == "hard"
    assert failures and "amount_underflow" not in failures[0].failure_message


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
