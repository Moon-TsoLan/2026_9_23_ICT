import json
from pathlib import Path

from ict.candidates import seal_candidate
from ict.catalog import Catalog
from ict.html_context import parse_notice
from ict.money import parse_amount, parse_price_cell, parse_quantity
from ict.schemas import AnnouncementUnderstanding, PackageUnderstanding
from ict.steps.s03_plan import plan_projects
from ict.steps.s07_normalize import normalize_candidates
from ict.steps.s08_merge import merge_projects

HTML = Path(r"D:\all_contest\2026_9_23_ICT\data\赛题五基准测试数据\赛题五.基准测试数据_html\t20260202_26139731.html")


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


def test_merge_keeps_high_priority_and_winner_suppliers():
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
    assert project.cobs[0].unit_price == 200
    assert project.subs[0].cooperative_product_suppliers == ["厂商甲"]
    assert merged.conflicts


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
